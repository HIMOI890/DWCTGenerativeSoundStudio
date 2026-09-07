using System.Net.Http.Headers;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Services;

public interface IStudioSignedMediaUrlResolver
{
    ValueTask<ResolvedStudioProjectMedia> ResolveProjectMediaAsync(
        string projectId,
        string relativePath,
        CancellationToken cancellationToken = default);
}

public sealed record ResolvedStudioProjectMedia(string RelativePath, Uri? SignedUri)
{
    public bool UsesSignedUrl => SignedUri is not null;

    public static ResolvedStudioProjectMedia UseProtectedProjectPath(string relativePath)
        => new(RequireRelativePath(relativePath), null);

    public static ResolvedStudioProjectMedia UseSignedUrl(string relativePath, Uri signedUri)
    {
        ArgumentNullException.ThrowIfNull(signedUri);
        return new(RequireRelativePath(relativePath), signedUri);
    }

    private static string RequireRelativePath(string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            throw new ArgumentException("A project media path is required.", nameof(relativePath));
        }

        return relativePath.Trim();
    }
}

public sealed class PassthroughStudioSignedMediaUrlResolver : IStudioSignedMediaUrlResolver
{
    public ValueTask<ResolvedStudioProjectMedia> ResolveProjectMediaAsync(
        string projectId,
        string relativePath,
        CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return ValueTask.FromResult(ResolvedStudioProjectMedia.UseProtectedProjectPath(relativePath));
    }
}

public sealed class StudioApiSignedMediaUrlResolver : IStudioSignedMediaUrlResolver
{
    private static readonly HashSet<string> AudioExtensions = new(StringComparer.OrdinalIgnoreCase)
    {
        ".aac",
        ".flac",
        ".m4a",
        ".mp3",
        ".ogg",
        ".wav"
    };

    private readonly StudioApiClient _apiClient;

    public StudioApiSignedMediaUrlResolver(StudioApiClient apiClient)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
    }

    public async ValueTask<ResolvedStudioProjectMedia> ResolveProjectMediaAsync(
        string projectId,
        string relativePath,
        CancellationToken cancellationToken = default)
    {
        string normalizedPath = RequireRelativePath(relativePath);
        try
        {
            Uri signedUri = await _apiClient.GetProjectMediaUrlAsync(
                    projectId,
                    new SignedMediaUrlRequest
                    {
                        Purpose = DeterminePurpose(normalizedPath),
                        Path = normalizedPath
                    },
                    cancellationToken)
                .ConfigureAwait(false);
            return ResolvedStudioProjectMedia.UseSignedUrl(normalizedPath, signedUri);
        }
        catch (StudioApiException exception) when (StudioApiClient.ShouldFallbackToLegacyProjectFileRoute(exception))
        {
            return ResolvedStudioProjectMedia.UseProtectedProjectPath(normalizedPath);
        }
    }

    private static string DeterminePurpose(string relativePath)
    {
        if (relativePath.StartsWith("assets/audio/", StringComparison.OrdinalIgnoreCase) ||
            relativePath.StartsWith("assets\\audio\\", StringComparison.OrdinalIgnoreCase))
        {
            return "audio";
        }

        return AudioExtensions.Contains(Path.GetExtension(relativePath))
            ? "audio"
            : "file";
    }

    private static string RequireRelativePath(string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            throw new ArgumentException("A project media path is required.", nameof(relativePath));
        }

        return relativePath.Trim();
    }
}

public sealed class StudioProjectMediaClient : IDisposable
{
    private readonly StudioApiClient _apiClient;
    private readonly IStudioSignedMediaUrlResolver _signedMediaResolver;
    private readonly HttpClient _httpClient;
    private readonly bool _ownsClient;

    public StudioProjectMediaClient(
        StudioApiClient apiClient,
        IStudioSignedMediaUrlResolver? signedMediaResolver = null,
        HttpClient? httpClient = null)
    {
        _apiClient = apiClient ?? throw new ArgumentNullException(nameof(apiClient));
        _signedMediaResolver = signedMediaResolver ?? new PassthroughStudioSignedMediaUrlResolver();
        _httpClient = httpClient ?? new HttpClient();
        _ownsClient = httpClient is null;
        _httpClient.Timeout = Timeout.InfiniteTimeSpan;
    }

    public async Task<TResult> StreamProjectMediaAsync<TResult>(
        string projectId,
        string relativePath,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(callback);

        string normalizedPath = RequireRelativePath(relativePath);
        ResolvedStudioProjectMedia resolution = await _signedMediaResolver
            .ResolveProjectMediaAsync(projectId, normalizedPath, cancellationToken)
            .ConfigureAwait(false)
            ?? ResolvedStudioProjectMedia.UseProtectedProjectPath(normalizedPath);

        if (!resolution.UsesSignedUrl)
        {
            return await _apiClient
                .StreamProjectFileAsync(projectId, RequireRelativePath(resolution.RelativePath), callback, cancellationToken)
                .ConfigureAwait(false);
        }

        return await StreamSignedMediaAsync(resolution.SignedUri!, callback, cancellationToken).ConfigureAwait(false);
    }

    public void Dispose()
    {
        if (_ownsClient)
        {
            _httpClient.Dispose();
        }
    }

    private async Task<TResult> StreamSignedMediaAsync<TResult>(
        Uri signedUri,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken)
    {
        ValidateSignedUri(signedUri);

        using var request = new HttpRequestMessage(HttpMethod.Get, signedUri);
        request.Headers.Accept.Clear();
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("*/*"));

        using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken)
            .ConfigureAwait(false);
        await StudioApiClient.EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var scopedFile = new StudioFileStream(
            stream,
            response.Content.Headers,
            response.Headers,
            response.StatusCode);
        return await callback(scopedFile, cancellationToken).ConfigureAwait(false);
    }

    private static string RequireRelativePath(string relativePath)
    {
        if (string.IsNullOrWhiteSpace(relativePath))
        {
            throw new ArgumentException("A project media path is required.", nameof(relativePath));
        }

        return relativePath.Trim();
    }

    private static void ValidateSignedUri(Uri signedUri)
    {
        ArgumentNullException.ThrowIfNull(signedUri);
        if (!signedUri.IsAbsoluteUri
            || (!string.Equals(signedUri.Scheme, Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
                && !string.Equals(signedUri.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)))
        {
            throw new InvalidOperationException(
                "Resolved signed media URLs must be absolute http:// or https:// addresses.");
        }
    }
}
