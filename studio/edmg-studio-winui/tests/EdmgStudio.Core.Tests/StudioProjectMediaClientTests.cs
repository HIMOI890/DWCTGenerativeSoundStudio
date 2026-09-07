using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using EdmgStudio.Core.Services;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class StudioProjectMediaClientTests
{
    [TestMethod]
    public async Task StreamProjectMediaAsync_UsesAuthenticatedProjectStreamWhenNoSignedUrlIsResolved()
    {
        byte[] expected = [0x10, 0x20, 0x30];
        HttpRequestMessage? captured = null;
        using var apiHttpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            captured = request;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(expected)
            });
        }));
        using var apiClient = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            apiHttpClient);
        using var mediaClient = new StudioProjectMediaClient(
            apiClient,
            new PassthroughStudioSignedMediaUrlResolver(),
            new HttpClient(new RecordingHandler((_, _) =>
                throw new AssertFailedException("Signed URL HTTP client should not be used when the resolver falls back to the project file API."))));

        byte[] actual = await mediaClient.StreamProjectMediaAsync(
            "project /#1",
            "renders/final take #1.mp4",
            async (file, cancellationToken) =>
            {
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.IsNotNull(captured);
        Assert.AreEqual("/v1/projects/project%20%2F%231/file", captured.RequestUri!.AbsolutePath);
        Assert.AreEqual("?path=renders%2Ffinal%20take%20%231.mp4", captured.RequestUri.Query);
        Assert.AreEqual("Bearer", captured.Headers.Authorization?.Scheme);
        Assert.AreEqual("stream-token", captured.Headers.Authorization?.Parameter);
    }

    [TestMethod]
    public async Task StreamProjectMediaAsync_UsesResolvedSignedUrlWithoutAuthorization()
    {
        byte[] expected = [0xAB, 0xCD];
        var content = new TrackingContent(expected);
        var apiCalls = 0;
        HttpRequestMessage? captured = null;
        using var apiHttpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            apiCalls++;
            throw new AssertFailedException("The authenticated project file endpoint should not be used when a signed URL is resolved.");
        }));
        using var signedHttpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            captured = request;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
        }));
        using var apiClient = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            apiHttpClient);
        using var mediaClient = new StudioProjectMediaClient(
            apiClient,
            new StaticSignedMediaResolver(ResolvedStudioProjectMedia.UseSignedUrl(
                "renders/preview.png",
                new Uri("https://cdn.example.invalid/media/preview.png?sig=abc"))),
            signedHttpClient);

        byte[] actual = await mediaClient.StreamProjectMediaAsync(
            "p1",
            "renders/preview.png",
            async (file, cancellationToken) =>
            {
                Assert.AreEqual(expected.Length, file.ContentHeaders.ContentLength);
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.AreEqual(0, apiCalls);
        Assert.IsNotNull(captured);
        Assert.AreEqual(new Uri("https://cdn.example.invalid/media/preview.png?sig=abc"), captured.RequestUri);
        Assert.IsNull(captured.Headers.Authorization);
        Assert.IsTrue(content.IsDisposed);
        Assert.IsTrue(content.Stream.IsDisposed);
    }

    [TestMethod]
    public async Task StreamProjectMediaAsync_RejectsUnsupportedSignedUrlSchemes()
    {
        using var apiClient = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            new HttpClient(new RecordingHandler((_, _) =>
                throw new AssertFailedException("API HTTP client should not be called when validation fails first."))));
        using var mediaClient = new StudioProjectMediaClient(
            apiClient,
            new StaticSignedMediaResolver(ResolvedStudioProjectMedia.UseSignedUrl(
                "renders/preview.png",
                new Uri("file:///E:/secret-preview.png"))),
            new HttpClient(new RecordingHandler((_, _) =>
                throw new AssertFailedException("Signed URL HTTP client should not be called when validation fails first."))));

        InvalidOperationException exception = await Assert.ThrowsExactlyAsync<InvalidOperationException>(
            () => mediaClient.StreamProjectMediaAsync(
                "p1",
                "renders/preview.png",
                (_, _) => Task.FromResult(false)));

        StringAssert.Contains(exception.Message, "http:// or https://");
    }

    [TestMethod]
    public async Task StreamProjectMediaAsync_UsesStudioApiSignedMediaResolverForNativeMedia()
    {
        byte[] expected = [0x44, 0x55];
        HttpRequestMessage? signedRequest = null;
        string? signedMediaRequestBody = null;
        using var apiHttpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            if (request.Method == HttpMethod.Post &&
                request.RequestUri?.AbsolutePath == "/v1/projects/p1/media-urls")
            {
                signedMediaRequestBody = await request.Content!.ReadAsStringAsync(cancellationToken);
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = JsonContent.Create(new
                    {
                        expires_at = 1760000000L,
                        urls = new[]
                        {
                            new
                            {
                                purpose = "file",
                                url = "/signed/native-preview.mp4?sig=abc"
                            }
                        }
                    })
                };
            }

            throw new AssertFailedException("Only the signed-media contract should use the API client.");
        }));
        using var signedHttpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            signedRequest = request;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(expected)
            });
        }));
        using var apiClient = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("preview-token"),
            apiHttpClient);
        using var mediaClient = new StudioProjectMediaClient(
            apiClient,
            new StudioApiSignedMediaUrlResolver(apiClient),
            signedHttpClient);

        byte[] actual = await mediaClient.StreamProjectMediaAsync(
            "p1",
            "renders/native-preview.mp4",
            async (file, cancellationToken) =>
            {
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.IsNotNull(signedRequest);
        Assert.AreEqual("/signed/native-preview.mp4", signedRequest.RequestUri!.AbsolutePath);
        Assert.IsNull(signedRequest.Headers.Authorization);
        Assert.IsNotNull(signedMediaRequestBody);
        using JsonDocument payload = JsonDocument.Parse(signedMediaRequestBody);
        JsonElement request = payload.RootElement.GetProperty("requests")[0];
        Assert.AreEqual("file", request.GetProperty("purpose").GetString());
        Assert.AreEqual("renders/native-preview.mp4", request.GetProperty("path").GetString());
    }

    [TestMethod]
    public async Task StreamProjectMediaAsync_FallsBackWhenSignedMediaContractIsUnavailable()
    {
        byte[] expected = [0x21, 0x22];
        int signedGetCalls = 0;
        var capturedApiUris = new List<Uri>();
        using var apiHttpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            capturedApiUris.Add(request.RequestUri!);
            return Task.FromResult((request.Method.Method, request.RequestUri!.AbsolutePath) switch
            {
                ("POST", "/v1/projects/p1/media-urls") => new HttpResponseMessage(HttpStatusCode.NotFound)
                {
                    Content = JsonContent.Create(new { error = new { code = "NOT_FOUND", message = "missing" } })
                },
                ("GET", "/v1/projects/p1/file") => new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new ByteArrayContent(expected)
                },
                _ => new HttpResponseMessage(HttpStatusCode.NotFound)
            });
        }));
        using var signedHttpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            signedGetCalls++;
            throw new AssertFailedException("Signed download HTTP client should not be used after a fallback.");
        }));
        using var apiClient = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("preview-token"),
            apiHttpClient);
        using var mediaClient = new StudioProjectMediaClient(
            apiClient,
            new StudioApiSignedMediaUrlResolver(apiClient),
            signedHttpClient);

        byte[] actual = await mediaClient.StreamProjectMediaAsync(
            "p1",
            "renders/native-preview.mp4",
            async (file, cancellationToken) =>
            {
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.AreEqual(0, signedGetCalls);
        CollectionAssert.AreEqual(
            new[]
            {
                "/v1/projects/p1/media-urls",
                "/v1/projects/p1/file"
            },
            capturedApiUris.Select(uri => uri.AbsolutePath).ToArray());
        Assert.AreEqual("?path=renders%2Fnative-preview.mp4", capturedApiUris[1].Query);
    }

    private sealed class StaticEndpointProvider(Uri backendUri) : IBackendEndpointProvider
    {
        public Uri CurrentBackendUri { get; } = backendUri;
    }

    private sealed class StaticTokenProvider(string? token) : IBackendTokenProvider
    {
        public ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken = default)
            => ValueTask.FromResult(token);
    }

    private sealed class StaticSignedMediaResolver(ResolvedStudioProjectMedia resolution) : IStudioSignedMediaUrlResolver
    {
        public ValueTask<ResolvedStudioProjectMedia> ResolveProjectMediaAsync(
            string projectId,
            string relativePath,
            CancellationToken cancellationToken = default)
        {
            cancellationToken.ThrowIfCancellationRequested();
            return ValueTask.FromResult(resolution);
        }
    }

    private sealed class RecordingHandler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> callback) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            => callback(request, cancellationToken);
    }

    private sealed class TrackingContent : HttpContent
    {
        private readonly byte[] _bytes;

        public TrackingContent(byte[] bytes)
        {
            _bytes = bytes;
            Headers.ContentLength = bytes.Length;
            Stream = new TrackingStream(bytes);
        }

        public bool IsDisposed { get; private set; }

        public TrackingStream Stream { get; }

        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context)
            => stream.WriteAsync(_bytes).AsTask();

        protected override bool TryComputeLength(out long length)
        {
            length = _bytes.Length;
            return true;
        }

        protected override Task<Stream> CreateContentReadStreamAsync()
            => Task.FromResult<Stream>(Stream);

        protected override void Dispose(bool disposing)
        {
            IsDisposed = true;
            base.Dispose(disposing);
        }
    }

    private sealed class TrackingStream(byte[] bytes) : MemoryStream(bytes)
    {
        public bool IsDisposed { get; private set; }

        protected override void Dispose(bool disposing)
        {
            IsDisposed = true;
            base.Dispose(disposing);
        }
    }
}
