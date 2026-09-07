using System.Net;
using System.Net.Http;
using System.Text;
using EdmgStudio.Core.Services;
using EdmgStudio.Core.Media;
using EdmgStudio.WinUI.Graphics;

namespace EdmgStudio.WinUI.Services;

public sealed class AppServices : IAsyncDisposable
{
    private readonly object _previewSessionsSync = new();
    private readonly HashSet<PreviewRendererSession> _previewSessions = [];
    private readonly HashSet<VideoPlaybackSession> _videoPlaybackSessions = [];
    private readonly HttpClient _apiHttpClient;
    private bool _isDisposing;

    private AppServices(
        BackendConfiguration configuration,
        BackendSupervisor backendSupervisor,
        StudioApiClient apiClient,
        HttpClient apiHttpClient,
        StudioProjectMediaClient projectMediaClient,
        StudioSessionService session)
    {
        Configuration = configuration;
        BackendSupervisor = backendSupervisor;
        ApiClient = apiClient;
        _apiHttpClient = apiHttpClient;
        ProjectMediaClient = projectMediaClient;
        Session = session;
    }

    public BackendConfiguration Configuration { get; }
    public BackendSupervisor BackendSupervisor { get; }
    public StudioApiClient ApiClient { get; }
    public StudioProjectMediaClient ProjectMediaClient { get; }
    public StudioSessionService Session { get; }

    internal bool TryTrackPreviewSession(PreviewRendererSession session)
    {
        ArgumentNullException.ThrowIfNull(session);
        lock (_previewSessionsSync)
        {
            if (_isDisposing)
            {
                return false;
            }

            return _previewSessions.Add(session);
        }
    }

    internal void UntrackPreviewSession(PreviewRendererSession session)
    {
        lock (_previewSessionsSync)
        {
            _previewSessions.Remove(session);
        }
    }

    internal bool TryTrackVideoPlaybackSession(VideoPlaybackSession session)
    {
        ArgumentNullException.ThrowIfNull(session);
        lock (_previewSessionsSync)
        {
            if (_isDisposing)
            {
                return false;
            }

            return _videoPlaybackSessions.Add(session);
        }
    }

    internal void UntrackVideoPlaybackSession(VideoPlaybackSession session)
    {
        lock (_previewSessionsSync)
        {
            _videoPlaybackSessions.Remove(session);
        }
    }

    public static AppServices Create()
    {
        var configuration = BackendConfiguration.Load();
        var tokenProvider = new WindowsBackendTokenProvider(new EnvironmentBackendTokenProvider());
        var launchToken = tokenProvider.GetTokenAsync().AsTask().GetAwaiter().GetResult();
        if (!string.IsNullOrWhiteSpace(launchToken))
        {
            var managedEnvironment = new Dictionary<string, string>(configuration.ManagedEnvironment, StringComparer.OrdinalIgnoreCase)
            {
                ["EDMG_BACKEND_AUTH_TOKEN"] = launchToken
            };
            configuration = configuration with { ManagedEnvironment = managedEnvironment };
        }

        var supervisor = new BackendSupervisor(configuration);

        // Convert transport-level connection failures into a normal HTTP 503 response.
        // StudioApiClient already converts non-success HTTP responses into StudioApiException,
        // which the WinUI pages know how to display without letting an async event handler
        // crash the shell when the local backend is still starting or temporarily offline.
        var apiHttpClient = new HttpClient(new BackendAvailabilityHandler())
        {
            Timeout = Timeout.InfiniteTimeSpan
        };
        var apiClient = new StudioApiClient(supervisor, tokenProvider, apiHttpClient);

        return new AppServices(
            configuration,
            supervisor,
            apiClient,
            apiHttpClient,
            new StudioSessionService());
        var apiClient = new StudioApiClient(supervisor, tokenProvider);
        var projectMediaClient = new StudioProjectMediaClient(apiClient, new StudioApiSignedMediaUrlResolver(apiClient));
        return new AppServices(configuration, supervisor, apiClient, projectMediaClient, new StudioSessionService());
    }

    public async ValueTask DisposeAsync()
    {
        List<Exception>? failures = null;
        PreviewRendererSession[] previewSessions;
        VideoPlaybackSession[] videoPlaybackSessions;
        lock (_previewSessionsSync)
        {
            if (_isDisposing)
            {
                return;
            }

            _isDisposing = true;
            videoPlaybackSessions = [.. _videoPlaybackSessions];
            _videoPlaybackSessions.Clear();
            previewSessions = [.. _previewSessions];
            _previewSessions.Clear();
        }

        foreach (VideoPlaybackSession session in videoPlaybackSessions)
        {
            try
            {
                await session.DisposeAsync();
            }
            catch (Exception exception)
            {
                (failures ??= []).Add(exception);
            }
        }

        foreach (PreviewRendererSession session in previewSessions)
        {
            try
            {
                await session.DisposeAsync();
            }
            catch (Exception exception)
            {
                (failures ??= []).Add(exception);
            }
        }

        try
        {
            await BackendSupervisor.DisposeAsync();
        }
        catch (Exception exception)
        {
            (failures ??= []).Add(exception);
        }

        try
        {
            ProjectMediaClient.Dispose();
        }
        catch (Exception exception)
        {
            (failures ??= []).Add(exception);
        }

        try
        {
            ApiClient.Dispose();
        }
        catch (Exception exception)
        {
            (failures ??= []).Add(exception);
        }

        try
        {
            _apiHttpClient.Dispose();
        }
        catch (Exception exception)
        {
            (failures ??= []).Add(exception);
        }

        if (failures is not null)
        {
            throw new AggregateException("One or more application services failed to shut down cleanly.", failures);
        }
    }
}

internal sealed class BackendAvailabilityHandler : DelegatingHandler
{
    public BackendAvailabilityHandler()
        : base(new SocketsHttpHandler())
    {
    }

    protected override async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        try
        {
            return await base.SendAsync(request, cancellationToken).ConfigureAwait(false);
        }
        catch (HttpRequestException exception) when (!cancellationToken.IsCancellationRequested)
        {
            CrashLogger.Write(
                $"Studio API transport could not reach {request.RequestUri}; returning a nonfatal 503 response.",
                exception);

            var body =
                "{\"error\":{\"code\":\"BACKEND_UNAVAILABLE\",\"message\":\"Studio backend is unavailable.\",\"hint\":\"Wait for the managed backend to finish starting, then retry.\"}}";

            return new HttpResponseMessage(HttpStatusCode.ServiceUnavailable)
            {
                RequestMessage = request,
                ReasonPhrase = "Studio backend unavailable",
                Content = new StringContent(body, Encoding.UTF8, "application/json")
            };
        }
    }
}
