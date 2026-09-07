using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization.Metadata;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Services;

public interface IBackendTokenProvider
{
    ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken = default);
}

public sealed class EnvironmentBackendTokenProvider : IBackendTokenProvider
{
    public ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var token = Environment.GetEnvironmentVariable("EDMG_BACKEND_AUTH_TOKEN");
        if (string.IsNullOrWhiteSpace(token))
        {
            token = Environment.GetEnvironmentVariable("EDMG_STUDIO_BACKEND_AUTH_TOKEN");
        }

        return ValueTask.FromResult<string?>(string.IsNullOrWhiteSpace(token) ? null : token.Trim());
    }
}

/// <summary>
/// Provides callback-scoped access to a streamed project file. The stream and
/// headers are valid only until the callback supplied to
/// <see cref="StudioApiClient.StreamProjectFileAsync{TResult}"/> completes.
/// </summary>
public sealed class StudioFileStream
{
    internal StudioFileStream(
        Stream stream,
        HttpContentHeaders contentHeaders,
        HttpResponseHeaders responseHeaders,
        HttpStatusCode statusCode)
    {
        Stream = stream;
        ContentHeaders = contentHeaders;
        ResponseHeaders = responseHeaders;
        StatusCode = statusCode;
    }

    public Stream Stream { get; }

    public HttpContentHeaders ContentHeaders { get; }

    public HttpResponseHeaders ResponseHeaders { get; }

    public HttpStatusCode StatusCode { get; }
}

public sealed class StudioApiClient : IDisposable
{
    private const string DefaultProjectMediaUrlsRelativePathTemplate = "/v1/projects/{0}/media-urls";
    private readonly IBackendEndpointProvider _endpointProvider;
    private readonly IBackendTokenProvider _tokenProvider;
    private readonly HttpClient _httpClient;
    private readonly bool _ownsClient;
    private readonly string _projectMediaUrlsRelativePathTemplate;

    public StudioApiClient(
        IBackendEndpointProvider endpointProvider,
        IBackendTokenProvider tokenProvider,
        HttpClient? httpClient = null,
        string? projectMediaUrlsRelativePathTemplate = null)
    {
        _endpointProvider = endpointProvider;
        _tokenProvider = tokenProvider;
        _httpClient = httpClient ?? new HttpClient();
        _ownsClient = httpClient is null;
        _projectMediaUrlsRelativePathTemplate = string.IsNullOrWhiteSpace(projectMediaUrlsRelativePathTemplate)
            ? DefaultProjectMediaUrlsRelativePathTemplate
            : projectMediaUrlsRelativePathTemplate;
        _httpClient.Timeout = Timeout.InfiniteTimeSpan;
    }

    public Task<HealthResponse> GetHealthAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<HealthResponse>(HttpMethod.Get, "/health", null, includeCredentials: false, cancellationToken);

    public Task<ProjectListResponse> GetProjectsAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<ProjectListResponse>(HttpMethod.Get, "/v1/projects", null, true, cancellationToken);

    public async Task<ProjectResponse> CreateProjectAsync(string name, CancellationToken cancellationToken = default)
    {
        var normalized = (name ?? string.Empty).Trim();
        if (normalized.Length is < 1 or > 200)
        {
            throw new ArgumentException("Project name must contain between 1 and 200 characters.", nameof(name));
        }

        return await SendJsonAsync<ProjectResponse>(
            HttpMethod.Post,
            "/v1/projects",
            JsonContent.Create(
                new CreateProjectRequest(normalized),
                StudioJson.GetTypeInfo<CreateProjectRequest>()),
            true,
            cancellationToken).ConfigureAwait(false);
    }

    public Task<ProjectResponse> GetProjectAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonAsync<ProjectResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}",
            null,
            true,
            cancellationToken);

    public async Task UploadAudioAsync(
        string projectId,
        Stream audioStream,
        string fileName,
        string? contentType = null,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(audioStream);
        if (!audioStream.CanRead)
        {
            throw new ArgumentException("The selected audio stream is not readable.", nameof(audioStream));
        }

        var safeFileName = Path.GetFileName(fileName);
        if (string.IsNullOrWhiteSpace(safeFileName))
        {
            throw new ArgumentException("The selected audio file must have a file name.", nameof(fileName));
        }

        using var multipart = new MultipartFormDataContent();
        using var streamContent = new StreamContent(audioStream);
        streamContent.Headers.ContentType = new MediaTypeHeaderValue(
            string.IsNullOrWhiteSpace(contentType) ? "application/octet-stream" : contentType);
        multipart.Add(streamContent, "file", safeFileName);

        using var request = await CreateRequestAsync(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/assets/audio",
            multipart,
            includeCredentials: true,
            cancellationToken).ConfigureAwait(false);
        using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
    }

    public Task<AnalysisResponse> AnalyzeAudioAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonAsync<AnalysisResponse>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/analyze_audio",
            new StringContent("{}", Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    public Task<PlanDto> GeneratePlanAsync(
        string projectId,
        PlanRequest request,
        string mode = "auto",
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (request.NumberOfVariants is < 1 or > 10)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "Plan variants must be between 1 and 10.");
        }

        if (request.MaximumScenes is < 1 or > 64)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "Maximum scenes must be between 1 and 64.");
        }

        var normalizedMode = PlannerWorkflow.NormalizeMode(mode);

        return SendJsonAsync<PlanDto>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/plan?mode={Uri.EscapeDataString(normalizedMode)}",
            JsonContent.Create(request, StudioJson.GetTypeInfo<PlanRequest>()),
            true,
            cancellationToken);
    }

    public Task<WorkspaceAssetsResponse> GetProjectAssetsAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<WorkspaceAssetsResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/assets",
            null,
            true,
            cancellationToken);

    public Task<ProjectHealthResponse> GetProjectHealthAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<ProjectHealthResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/health",
            null,
            true,
            cancellationToken);

    public Task<ProjectRelinkResponse> GetProjectRelinkSuggestionsAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<ProjectRelinkResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/health/relink",
            null,
            true,
            cancellationToken);

    public Task<ProjectCollectResponse> CollectProjectAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<ProjectCollectResponse>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/health/collect",
            new StringContent("{}", Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    public Task<MusicGraphResponse> GetProjectMusicGraphAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<MusicGraphResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/music_graph",
            null,
            true,
            cancellationToken);

    public Task<LiveCuesResponse> GetProjectLiveCuesAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<LiveCuesResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_cues",
            null,
            true,
            cancellationToken);

    public Task<LiveAssetsResponse> GetProjectLiveAssetsAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<LiveAssetsResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_assets",
            null,
            true,
            cancellationToken);

    public Task<ExportTemplatePackageResponse> ExportProjectTemplatePackageAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<ExportTemplatePackageResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/template_package/export",
            null,
            true,
            cancellationToken);

    public Task<ImportTemplatePackageResponse> ImportProjectTemplatePackageAsync(
        string projectId,
        TemplatePackageDto package,
        bool merge,
        long? expectedRevision,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<ImportTemplatePackageRequest, ImportTemplatePackageResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/template_package/import",
            new ImportTemplatePackageRequest
            {
                Package = package,
                Merge = merge,
                ExpectedRevision = expectedRevision,
            },
            cancellationToken);

    public Task<ImportTemplatePackageResponse> ImportProjectTemplatePackageAsync(
        string projectId,
        TemplatePackageDto package,
        bool merge,
        CancellationToken cancellationToken = default) =>
        ImportProjectTemplatePackageAsync(projectId, package, merge, null, cancellationToken);

    public Task<ApplyPlanToTimelineResponse> ApplyPlanToTimelineAsync(
        string projectId,
        int variantIndex,
        bool overwrite,
        long? expectedRevision,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<ApplyPlanToTimelineRequest, ApplyPlanToTimelineResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/timeline/apply_plan",
            new ApplyPlanToTimelineRequest
            {
                VariantIndex = variantIndex,
                Overwrite = overwrite,
                ExpectedRevision = expectedRevision,
            },
            cancellationToken);

    public Task<ApplyPlanToTimelineResponse> ApplyPlanToTimelineAsync(
        string projectId,
        int variantIndex,
        bool overwrite,
        CancellationToken cancellationToken = default) =>
        ApplyPlanToTimelineAsync(projectId, variantIndex, overwrite, null, cancellationToken);

    public Task<ApplyMotionGrammarResponse> ApplyMotionGrammarAsync(
        string projectId,
        IReadOnlyList<MotionPhraseRequest> phrases,
        bool overwriteMotionTrack,
        long? expectedRevision,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(phrases);
        if (phrases.Count == 0)
        {
            throw new ArgumentException("At least one motion phrase is required.", nameof(phrases));
        }

        return PostJsonAsync<ApplyMotionGrammarRequest, ApplyMotionGrammarResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/motion_grammar/apply",
            new ApplyMotionGrammarRequest(phrases, overwriteMotionTrack, expectedRevision),
            cancellationToken);
    }

    public Task<ApplyMotionGrammarResponse> ApplyMotionGrammarAsync(
        string projectId,
        IReadOnlyList<MotionPhraseRequest> phrases,
        bool overwriteMotionTrack,
        CancellationToken cancellationToken = default) =>
        ApplyMotionGrammarAsync(projectId, phrases, overwriteMotionTrack, null, cancellationToken);

    public Task<UpdatePlanVariantResponse> UpdatePlanVariantAsync(
        string projectId,
        int variantIndex,
        IReadOnlyList<PlanSceneDto> scenes,
        long? expectedRevision,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<UpdatePlanVariantRequest, UpdatePlanVariantResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/plan/variant",
            new UpdatePlanVariantRequest
            {
                VariantIndex = variantIndex,
                Scenes = scenes,
                ExpectedRevision = expectedRevision,
            },
            cancellationToken);

    public Task<UpdatePlanVariantResponse> UpdatePlanVariantAsync(
        string projectId,
        int variantIndex,
        IReadOnlyList<PlanSceneDto> scenes,
        CancellationToken cancellationToken = default) =>
        UpdatePlanVariantAsync(projectId, variantIndex, scenes, null, cancellationToken);

    public Task<JsonElement> ImportPlannerLabAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/planner_lab/import",
            request,
            cancellationToken);

    public Task<PlannerLabImportResponse> ImportPlannerLabAsync(
        string projectId,
        PlannerLabImportRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (request.Analysis.ValueKind != JsonValueKind.Object)
        {
            throw new ArgumentException("Planner analysis must be a JSON object.", nameof(request));
        }

        if (request.Plan.ValueKind != JsonValueKind.Object)
        {
            throw new ArgumentException("Planner plan must be a JSON object.", nameof(request));
        }

        return PostJsonAsync<PlannerLabImportRequest, PlannerLabImportResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/planner_lab/import",
            request,
            cancellationToken);
    }

    public Task<JsonElement> ApplyReactiveLabAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/reactive_lab/apply",
            request,
            cancellationToken);

    public Task<ReactiveLabApplyResponse> ApplyReactiveLabAsync(
        string projectId,
        ReactiveLabApplyRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (!ReactiveWorkflow.HasMeaningfulPayload(request))
        {
            throw new ArgumentException(
                "Reactive Lab requires keyframes, beats, cues, sections, schedules, repairs, or a handoff manifest.",
                nameof(request));
        }

        return PostJsonAsync<ReactiveLabApplyRequest, ReactiveLabApplyResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/reactive_lab/apply",
            request,
            cancellationToken);
    }

    public Task<JsonElement> TestAwsCloudAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/aws/test", request, cancellationToken);

    public Task<JsonElement> BundleAwsCloudAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/aws/bundle", request, cancellationToken);

    public Task<JsonElement> TestAzureCloudAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/azure/test", request, cancellationToken);

    public Task<JsonElement> GetHuggingFaceCloudSettingsAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/cloud/hf/settings", null, true, cancellationToken);

    public Task<JsonElement> SaveHuggingFaceCloudSettingsAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/hf/settings", request, cancellationToken);

    public Task<JsonElement> TestHuggingFaceCloudAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/hf/test", request, cancellationToken);

    public Task<JsonElement> BundleLightningCloudAsync(JsonElement request, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/cloud/lightning/bundle", request, cancellationToken);

    public Task<JsonElement> GetAiStatusAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/ai/status", null, true, cancellationToken);

    public Task<AiReadinessResponse> GetAiReadinessAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<AiReadinessResponse>(HttpMethod.Get, "/v1/ai/status", null, true, cancellationToken);

    public Task<JsonElement> GetComfyUiCapabilitiesAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/comfyui/capabilities", null, true, cancellationToken);

    public Task<JsonElement> GetUnrealPreviewAsync(
        string projectId,
        int variantIndex,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/unreal/preview?variant_index={Math.Max(0, variantIndex)}",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> GetLiveCuePublishStatusAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_cues/publish/status",
            null,
            true,
            cancellationToken);

    public Task<LiveCuePublishResponse> GetTypedLiveCuePublishStatusAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<LiveCuePublishResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_cues/publish/status",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> GetConfigAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/config", null, true, cancellationToken);

    public Task<SetupStatusResponse> GetSetupStatusAsync(
        bool refresh = false,
        bool includeOptional = false,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<SetupStatusResponse>(
            HttpMethod.Get,
            "/v1/setup/status" + BuildQuery(
                ("refresh", refresh ? "true" : "false"),
                ("include_optional", includeOptional ? "true" : "false")),
            null,
            true,
            cancellationToken);

    public Task<SetupTaskListResponse> GetSetupTasksAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<SetupTaskListResponse>(
            HttpMethod.Get,
            "/v1/setup/tasks",
            null,
            true,
            cancellationToken);

    public Task<SetupTaskActionResponse> CancelSetupTaskAsync(
        string taskId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<SetupTaskActionResponse>(
            HttpMethod.Post,
            $"/v1/setup/tasks/{EscapeIdentifier(taskId)}/cancel",
            null,
            true,
            cancellationToken);

    public Task<SetupTaskActionResponse> InstallManagedOllamaAsync(
        CancellationToken cancellationToken = default) =>
        PostEmptySetupActionAsync("/v1/setup/ollama/install_managed", cancellationToken);

    public Task<SetupTaskActionResponse> DownloadAndRunOllamaAsync(
        CancellationToken cancellationToken = default) =>
        PostEmptySetupActionAsync("/v1/setup/ollama/download_and_run", cancellationToken);

    public Task<SetupTaskActionResponse> StartManagedOllamaAsync(
        CancellationToken cancellationToken = default) =>
        PostEmptySetupActionAsync("/v1/setup/ollama/start_managed", cancellationToken);

    public Task<SetupTaskActionResponse> PullOllamaModelAsync(
        string model = "qwen3:8b",
        CancellationToken cancellationToken = default)
    {
        model = RequireShortValue(model, nameof(model), 200);
        return PostSetupActionAsync(
            "/v1/setup/ollama/pull",
            new SetupOllamaPullRequest(model),
            StudioJson.GetTypeInfo<SetupOllamaPullRequest>(),
            cancellationToken);
    }

    public Task<SetupTaskActionResponse> InstallSevenZipAsync(
        CancellationToken cancellationToken = default) =>
        PostEmptySetupActionAsync("/v1/setup/7zip/install", cancellationToken);

    public Task<SetupTaskActionResponse> InstallBackendAsync(
        string acceleratorProfile = "cpu",
        CancellationToken cancellationToken = default)
    {
        acceleratorProfile = ValidateSetupProfile(acceleratorProfile);
        return PostSetupActionAsync(
            "/v1/setup/backend/install",
            new SetupProfileRequest(acceleratorProfile),
            StudioJson.GetTypeInfo<SetupProfileRequest>(),
            cancellationToken);
    }

    public Task<SetupTaskActionResponse> InstallFullSetupAsync(
        string acceleratorProfile = "cpu",
        int comfyPort = 8188,
        string model = "qwen3:8b",
        CancellationToken cancellationToken = default)
    {
        acceleratorProfile = ValidateSetupProfile(acceleratorProfile);
        ValidatePort(comfyPort, nameof(comfyPort));
        model = RequireShortValue(model, nameof(model), 200);
        return PostSetupActionAsync(
            "/v1/setup/full/install",
            new SetupFullInstallRequest(acceleratorProfile, comfyPort, model),
            StudioJson.GetTypeInfo<SetupFullInstallRequest>(),
            cancellationToken);
    }

    public Task<SetupTaskActionResponse> InstallPortableComfyUiAsync(
        string flavor = "cpu",
        CancellationToken cancellationToken = default)
    {
        flavor = ValidateComfyUiFlavor(flavor, allowAuto: false);
        return PostSetupActionAsync(
            "/v1/setup/comfyui/portable/install",
            new SetupComfyUiInstallRequest(flavor),
            StudioJson.GetTypeInfo<SetupComfyUiInstallRequest>(),
            cancellationToken);
    }

    public Task<SetupTaskActionResponse> StartPortableComfyUiAsync(
        string flavor = "auto",
        int port = 8188,
        CancellationToken cancellationToken = default)
    {
        flavor = ValidateComfyUiFlavor(flavor, allowAuto: true);
        ValidatePort(port, nameof(port));
        return PostSetupActionAsync(
            "/v1/setup/comfyui/portable/start",
            new SetupComfyUiStartRequest(flavor, port),
            StudioJson.GetTypeInfo<SetupComfyUiStartRequest>(),
            cancellationToken);
    }

    public Task<SetupOperationResponse> StopPortableComfyUiAsync(
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<SetupOperationResponse>(
            HttpMethod.Post,
            "/v1/setup/comfyui/portable/stop",
            null,
            true,
            cancellationToken);

    public Task<SetupTaskActionResponse> InstallEdmgCoreAsync(
        string mode = "standard",
        string backend = "cpu",
        CancellationToken cancellationToken = default)
    {
        mode = RequireShortValue(mode, nameof(mode), 50).ToLowerInvariant();
        backend = RequireShortValue(backend, nameof(backend), 50).ToLowerInvariant();
        return PostSetupActionAsync(
            "/v1/setup/edmg/install",
            new SetupEdmgInstallRequest(mode, backend),
            StudioJson.GetTypeInfo<SetupEdmgInstallRequest>(),
            cancellationToken);
    }

    public Task<JsonElement> GetEdmgStatusAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/edmg/status", null, true, cancellationToken);

    public Task<JsonElement> GetTimelineAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/timeline",
            null,
            true,
            cancellationToken);

    public Task<TResult> StreamTimelineFrameAsync<TResult>(
        string projectId,
        double timeSeconds,
        int width,
        int height,
        bool force,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken = default)
    {
        if (!double.IsFinite(timeSeconds) || timeSeconds < 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(timeSeconds),
                "Timeline preview time must be finite and non-negative.");
        }

        if (width is < 1 or > 16_384)
        {
            throw new ArgumentOutOfRangeException(nameof(width));
        }

        if (height is < 1 or > 16_384)
        {
            throw new ArgumentOutOfRangeException(nameof(height));
        }

        string requestPath =
            $"/v1/projects/{EscapeIdentifier(projectId)}/preview/frame" +
            $"?t={timeSeconds.ToString("R", CultureInfo.InvariantCulture)}" +
            $"&w={width.ToString(CultureInfo.InvariantCulture)}" +
            $"&h={height.ToString(CultureInfo.InvariantCulture)}" +
            $"&force={(force ? "1" : "0")}";
        return StreamResponseAsync(requestPath, callback, cancellationToken);
    }

    public Task<JsonElement> SaveTimelineAsync(
        string projectId,
        JsonElement timeline,
        long? expectedRevision,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/timeline",
            new TimelineUpdateRequest(timeline, expectedRevision),
            StudioJson.GetTypeInfo<TimelineUpdateRequest>(),
            cancellationToken);

    public Task<JsonElement> SaveTimelineAsync(
        string projectId,
        JsonElement timeline,
        CancellationToken cancellationToken = default) =>
        SaveTimelineAsync(projectId, timeline, null, cancellationToken);

    public Task<JsonElement> AutosaveTimelineAsync(
        string projectId,
        JsonElement timeline,
        JsonElement? metadata,
        string? reason,
        long? expectedRevision,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/autosave",
            new TimelineAutosaveRequest(timeline, metadata, reason, expectedRevision),
            StudioJson.GetTypeInfo<TimelineAutosaveRequest>(),
            cancellationToken);

    public Task<JsonElement> AutosaveTimelineAsync(
        string projectId,
        JsonElement timeline,
        JsonElement? metadata = null,
        string? reason = null,
        CancellationToken cancellationToken = default) =>
        AutosaveTimelineAsync(projectId, timeline, metadata, reason, null, cancellationToken);

    public Task<TimelineRenderResponse> QueueTimelineRenderAsync(
        string projectId,
        TimelineRenderRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        var normalized = NormalizeTimelineRenderRequest(request);
        return SendJsonAsync<TimelineRenderResponse>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/timeline/render",
            JsonContent.Create(normalized, StudioJson.GetTypeInfo<TimelineRenderRequest>()),
            true,
            cancellationToken);
    }

    public Task<JsonElement> GetRecoveryAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/recovery",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> ApplyRecoveryAsync(
        string projectId,
        RecoveryApplyRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/recovery/apply",
            request,
            StudioJson.GetTypeInfo<RecoveryApplyRequest>(),
            cancellationToken);

    public Task<JsonElement> DiscardRecoveryAsync(string projectId, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/recovery/discard",
            new JsonObject(),
            cancellationToken);

    public Task<JsonElement> PreflightInternalRenderAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/internal/preflight",
            request,
            cancellationToken);

    public Task<JsonElement> StartInternalRenderAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/internal/video",
            request,
            cancellationToken);

    public Task<JsonElement> ValidatePipelineAsync(
        string projectId,
        PipelineRunOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= new PipelineRunOptions();
        return SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/pipeline/validate{BuildQuery(
                ("variant_index", Invariant(options.VariantIndex)),
                ("preset", options.Preset),
                ("mode", options.Mode),
                ("engine", options.Engine))}",
            null,
            true,
            cancellationToken);
    }

    public Task<JsonElement> RunPipelineAsync(
        string projectId,
        PipelineRunOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= new PipelineRunOptions();
        return SendJsonElementAsync(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/pipeline/run{BuildQuery(
                ("variant_index", Invariant(options.VariantIndex)),
                ("preset", options.Preset),
                ("mode", options.Mode),
                ("engine", options.Engine))}",
            null,
            true,
            cancellationToken);
    }

    public Task<JsonElement> GetRenderConductorPlanAsync(
        string projectId,
        int variantIndex = 0,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/conductor/plan{BuildQuery(
                ("variant_index", Invariant(variantIndex)))}",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> CreateRenderConductorPlanAsync(
        string projectId,
        RenderConductorPlanRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/conductor/plan",
            request,
            StudioJson.GetTypeInfo<RenderConductorPlanRequest>(),
            cancellationToken);

    public Task<JsonElement> PromoteRenderConductorPlanAsync(
        string projectId,
        RenderConductorPromoteRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/conductor/promote",
            request,
            StudioJson.GetTypeInfo<RenderConductorPromoteRequest>(),
            cancellationToken);

    public Task<JsonElement> GetRenderConductorContinuityAsync(
        string projectId,
        int variantIndex = 0,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/conductor/continuity{BuildQuery(
                ("variant_index", Invariant(variantIndex)))}",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> GetRenderPerformerPlanAsync(
        string projectId,
        int variantIndex = 0,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/performer/plan{BuildQuery(
                ("variant_index", Invariant(variantIndex)))}",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> CreateRenderPerformerPlanAsync(
        string projectId,
        PerformerWorkflowPlanRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/performer/plan",
            request,
            StudioJson.GetTypeInfo<PerformerWorkflowPlanRequest>(),
            cancellationToken);

    public Task<JsonElement> RunRenderPerformerAsync(
        string projectId,
        PerformerWorkflowRunRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/performer/run",
            request,
            StudioJson.GetTypeInfo<PerformerWorkflowRunRequest>(),
            cancellationToken);

    public Task<JsonElement> GetMotionSequencerAsync(
        string projectId,
        MotionSequencerOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= new MotionSequencerOptions();
        return SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/motion_sequencer{BuildQuery(
                ("variant_index", Invariant(options.VariantIndex)),
                ("fps", Invariant(options.Fps)))}",
            null,
            true,
            cancellationToken);
    }

    public Task<JsonElement> ApplyMotionSequencerAsync(
        string projectId,
        ParseqMotionApplyRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/motion_sequencer/apply",
            request,
            StudioJson.GetTypeInfo<ParseqMotionApplyRequest>(),
            cancellationToken);

    public Task<JsonElement> AutoRenderAsync(
        string projectId,
        AutoAnimateRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/auto",
            request,
            StudioJson.GetTypeInfo<AutoAnimateRequest>(),
            cancellationToken);

    public Task<JsonElement> AnimateLayersAsync(
        string projectId,
        LayeredAnimateRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/animate_layers",
            request,
            StudioJson.GetTypeInfo<LayeredAnimateRequest>(),
            cancellationToken);

    public Task<JsonElement> RenderSceneStillsAsync(
        string projectId,
        RenderScenesRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/stills/scenes",
            request,
            StudioJson.GetTypeInfo<RenderScenesRequest>(),
            cancellationToken);

    public Task<JsonElement> RenderComfyUiMotionScenesAsync(
        string projectId,
        RenderMotionRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/comfyui/motion_scenes",
            request,
            StudioJson.GetTypeInfo<RenderMotionRequest>(),
            cancellationToken);

    public Task<JsonElement> RenderSmartVideoAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/video/smart",
            request,
            cancellationToken);

    public Task<JsonElement> RenderTensorRtStandaloneAsync(
        string projectId,
        TensorRtStandaloneRenderRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/tensorrt-standalone",
            request,
            StudioJson.GetTypeInfo<TensorRtStandaloneRenderRequest>(),
            cancellationToken);

    public Task<JsonElement> PreviewTensorRtStandaloneAsync(
        string projectId,
        TensorRtStandaloneRenderRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/render/tensorrt-standalone/preview",
            request,
            StudioJson.GetTypeInfo<TensorRtStandaloneRenderRequest>(),
            cancellationToken);

    public Task<JsonElement> AssembleVideoAsync(
        string projectId,
        AssembleVideoRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/assemble_video",
            request,
            StudioJson.GetTypeInfo<AssembleVideoRequest>(),
            cancellationToken);

    public Task<JsonElement> ExportDeforumAsync(
        string projectId,
        ExportDeforumRequest request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/export/deforum",
            request,
            StudioJson.GetTypeInfo<ExportDeforumRequest>(),
            cancellationToken);

    public Task<JsonElement> ExportComfyUiWorkflowsAsync(
        string projectId,
        ComfyUiWorkflowExportOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        options ??= new ComfyUiWorkflowExportOptions();
        return SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/export/comfyui_workflows{BuildQuery(
                ("variant_index", Invariant(options.VariantIndex)),
                ("model_id", options.ModelId),
                ("workflow_family", options.WorkflowFamily),
                ("source_asset", options.SourceAsset),
                ("reference_asset", options.ReferenceAsset),
                ("inpaint_mask", options.InpaintMask),
                ("controlnet_model", options.ControlnetModel),
                ("conditioning_mode", options.ConditioningMode),
                ("width", Invariant(options.Width)),
                ("height", Invariant(options.Height)),
                ("steps", Invariant(options.Steps)),
                ("cfg", Invariant(options.Cfg)),
                ("sampler", options.Sampler),
                ("negative_prompt", options.NegativePrompt),
                ("seed", options.Seed is null ? null : Invariant(options.Seed.Value)),
                ("denoise_strength", Invariant(options.DenoiseStrength)),
                ("loras_json", options.LorasJson),
                ("outpaint_json", options.OutpaintJson),
                ("controlnet_units_json", options.ControlnetUnitsJson),
                ("hires_fix_json", options.HiresFixJson),
                ("refiner_json", options.RefinerJson),
                ("upscaler", options.Upscaler))}",
            null,
            true,
            cancellationToken);
    }

    public Task<JsonElement> UploadReferenceAssetAsync(
        string projectId,
        Stream content,
        string fileName,
        string? contentType = null,
        CancellationToken cancellationToken = default) =>
        UploadProjectAssetAsync(projectId, "refs", content, fileName, contentType, cancellationToken);

    public Task<JsonElement> UploadMaskAssetAsync(
        string projectId,
        Stream content,
        string fileName,
        string? contentType = null,
        CancellationToken cancellationToken = default) =>
        UploadProjectAssetAsync(projectId, "mask", content, fileName, contentType, cancellationToken);

    public Task<JsonElement> UploadOverlayAssetAsync(
        string projectId,
        Stream content,
        string fileName,
        string? contentType = null,
        CancellationToken cancellationToken = default) =>
        UploadProjectAssetAsync(projectId, "overlay", content, fileName, contentType, cancellationToken);

    public Task<JsonElement> TickWorkerAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Post, "/v1/jobs/tick", null, true, cancellationToken);

    public Task<JsonElement> VerifyEdmgAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Post, "/v1/edmg/verify", null, true, cancellationToken);

    public Task<StudioJobListResponse> GetJobsAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<StudioJobListResponse>(HttpMethod.Get, "/v1/jobs", null, true, cancellationToken);

    public Task<StudioJobListResponse> GetProjectJobsAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<StudioJobListResponse>(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/jobs",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> GetProjectJobAsync(
        string projectId,
        string jobId,
        int tailLines = 80,
        CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/jobs/{EscapeIdentifier(jobId)}?tail_lines={Math.Clamp(tailLines, 0, 5000)}",
            null,
            true,
            cancellationToken);

    public Task<StudioJobActionResponse> CancelJobAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostJobActionAsync(projectId, jobId, "cancel", cancellationToken);

    public Task<StudioJobActionResponse> PauseJobAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostJobActionAsync(projectId, jobId, "pause", cancellationToken);

    public Task<StudioJobActionResponse> ResumeJobAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostJobActionAsync(projectId, jobId, "resume", cancellationToken);

    public Task<StudioJobActionResponse> RetryJobAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostJobActionAsync(projectId, jobId, "retry", cancellationToken);

    public Task<JsonElement> ResumeJobFromCheckpointAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostProjectJobJsonAsync(projectId, jobId, "resume_from_checkpoint", cancellationToken);

    public Task<JsonElement> RestartJobCleanAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostProjectJobJsonAsync(projectId, jobId, "restart_clean", cancellationToken);

    public Task<JsonElement> ClearJobCachedFramesAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostProjectJobJsonAsync(projectId, jobId, "clear_cached_frames", cancellationToken);

    public Task<JsonElement> DropJobCheckpointAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        PostProjectJobJsonAsync(projectId, jobId, "drop_checkpoint", cancellationToken);

    public Task<JsonElement> GetJobLogAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        GetProjectJobJsonAsync(projectId, jobId, "log", cancellationToken);

    public Task<JsonElement> GetJobEventsAsync(
        string projectId,
        string jobId,
        CancellationToken cancellationToken = default) =>
        GetProjectJobJsonAsync(projectId, jobId, "events", cancellationToken);

    public Task<JsonElement> GetOutputsAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/outputs",
            null,
            true,
            cancellationToken);

    public async Task<byte[]> DownloadProjectFileAsync(
        string projectId,
        string relativePath,
        CancellationToken cancellationToken = default)
    {
        return await StreamProjectFileAsync(
                projectId,
                relativePath,
                async (file, callbackCancellationToken) =>
                {
                    using var destination = new MemoryStream();
                    await file.Stream.CopyToAsync(destination, callbackCancellationToken).ConfigureAwait(false);
                    return destination.ToArray();
                },
                cancellationToken)
            .ConfigureAwait(false);
    }

    public Task<SignedMediaUrlBatchResponse> GetProjectMediaUrlsAsync(
        string projectId,
        IEnumerable<SignedMediaUrlRequest> requests,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(requests);
        SignedMediaUrlRequest[] normalizedRequests = requests
            .Select(NormalizeSignedMediaUrlRequest)
            .ToArray();
        if (normalizedRequests.Length == 0)
        {
            throw new ArgumentException("At least one signed media request is required.", nameof(requests));
        }

        return PostJsonAsync<SignedMediaUrlBatchRequest, SignedMediaUrlBatchResponse>(
            string.Format(
                CultureInfo.InvariantCulture,
                _projectMediaUrlsRelativePathTemplate,
                EscapeIdentifier(projectId)),
            new SignedMediaUrlBatchRequest
            {
                Requests = [.. normalizedRequests]
            },
            cancellationToken);
    }

    public async Task<Uri> GetProjectMediaUrlAsync(
        string projectId,
        SignedMediaUrlRequest request,
        CancellationToken cancellationToken = default)
    {
        SignedMediaUrlRequest normalizedRequest = NormalizeSignedMediaUrlRequest(request);
        SignedMediaUrlBatchResponse response = await GetProjectMediaUrlsAsync(
                projectId,
                [normalizedRequest],
                cancellationToken)
            .ConfigureAwait(false);
        SignedMediaUrlResponse resolved = response.Urls.SingleOrDefault()
            ?? throw new InvalidOperationException("Studio did not return a signed media URL.");
        if (!string.Equals(resolved.Purpose, normalizedRequest.Purpose, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Studio returned a signed media URL for an unexpected purpose.");
        }

        return ResolveStreamTarget(resolved.Url);
    }

    public async Task<TResult> StreamProjectPreviewFileAsync<TResult>(
        string projectId,
        string relativePath,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken = default)
    {
        string normalizedPath = RequireValue(relativePath, nameof(relativePath));
        try
        {
            Uri signedTarget = await GetProjectMediaUrlAsync(
                    projectId,
                    new SignedMediaUrlRequest
                    {
                        Purpose = "file",
                        Path = normalizedPath
                    },
                    cancellationToken)
                .ConfigureAwait(false);
            return await StreamResponseAsync(signedTarget, callback, cancellationToken).ConfigureAwait(false);
        }
        catch (StudioApiException exception) when (ShouldFallbackToLegacyProjectFileRoute(exception))
        {
            return await StreamProjectFileAsync(projectId, normalizedPath, callback, cancellationToken).ConfigureAwait(false);
        }
    }

    public async Task<TResult> StreamProjectFileAsync<TResult>(
        string projectId,
        string relativePath,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken = default)
    {
        var path = RequireValue(relativePath, nameof(relativePath));
        var requestPath =
            $"/v1/projects/{EscapeIdentifier(projectId)}/file?path={Uri.EscapeDataString(path)}";
        return await StreamResponseAsync(requestPath, callback, cancellationToken).ConfigureAwait(false);
    }

    private async Task<TResult> StreamResponseAsync<TResult>(
        Uri requestUri,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(requestUri);
        ArgumentNullException.ThrowIfNull(callback);
        using var request = new HttpRequestMessage(HttpMethod.Get, requestUri);
        request.Headers.Accept.Clear();
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("*/*"));

        using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var scopedFile = new StudioFileStream(
            stream,
            response.Content.Headers,
            response.Headers,
            response.StatusCode);
        return await callback(scopedFile, cancellationToken).ConfigureAwait(false);
    }

    private async Task<TResult> StreamResponseAsync<TResult>(
        string requestPath,
        Func<StudioFileStream, CancellationToken, Task<TResult>> callback,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(callback);
        using var request = await CreateRequestAsync(
                HttpMethod.Get,
                requestPath,
                null,
                true,
                cancellationToken)
            .ConfigureAwait(false);
        request.Headers.Accept.Clear();
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("*/*"));

        using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var scopedFile = new StudioFileStream(
            stream,
            response.Content.Headers,
            response.Headers,
            response.StatusCode);
        return await callback(scopedFile, cancellationToken).ConfigureAwait(false);
    }

    public Task<JsonElement> GetVariantReviewAsync(string projectId, CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/variant_review",
            null,
            true,
            cancellationToken);

    public Task<JsonElement> SaveVariantDecisionAsync(
        string projectId,
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/variant_review/decision",
            request,
            cancellationToken);

    public Task<JsonElement> SaveVariantDecisionAsync(
        string projectId,
        JsonObject request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/variant_review/decision",
            request,
            cancellationToken);

    public Task<VariantReviewDecisionResponse> SaveVariantDecisionAsync(
        string projectId,
        VariantReviewDecisionRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateVariantReviewDecision(request);
        return PostJsonAsync<VariantReviewDecisionRequest, VariantReviewDecisionResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/variant_review/decision",
            request,
            cancellationToken);
    }

    public Task<LiveCuePublishResponse> StartLiveCuePublishAsync(
        string projectId,
        LiveCuePublishRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateLiveCuePublish(request);
        return PostJsonAsync<LiveCuePublishRequest, LiveCuePublishResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_cues/publish/start",
            request,
            cancellationToken);
    }

    public Task<LiveCuePublishResponse> StopLiveCuePublishAsync(
        string projectId,
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<LiveCuePublishResponse>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/live_cues/publish/stop",
            null,
            true,
            cancellationToken);

    public Task<WorldAdapterExportResponse> ExportWorldAdapterAsync(
        string projectId,
        WorldAdapterExportRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateWorldAdapterExport(request);
        return PostJsonAsync<WorldAdapterExportRequest, WorldAdapterExportResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/world_adapters/export",
            request,
            cancellationToken);
    }

    public Task<UnrealBundleExportResponse> ExportUnrealBundleAsync(
        string projectId,
        UnrealBundleExportRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateUnrealBundleExport(request);
        return PostJsonAsync<UnrealBundleExportRequest, UnrealBundleExportResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/export/unreal",
            request,
            cancellationToken);
    }

    public Task<UnrealImportPlanResponse> BuildUnrealImportPlanAsync(
        string projectId,
        UnrealImportPlanRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateUnrealImportPlan(request);
        return PostJsonAsync<UnrealImportPlanRequest, UnrealImportPlanResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/unreal/import-plan",
            request,
            cancellationToken);
    }

    public Task<UnrealReturnImportResponse> ImportUnrealReturnsAsync(
        string projectId,
        UnrealReturnImportRequest request,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        ValidateUnrealReturnImport(request);
        return PostJsonAsync<UnrealReturnImportRequest, UnrealReturnImportResponse>(
            $"/v1/projects/{EscapeIdentifier(projectId)}/import/unreal",
            request,
            cancellationToken);
    }

    public Task<JsonElement> GetContinuityAsync(string projectId, CancellationToken cancellationToken = default) =>
        GetRenderConductorContinuityAsync(projectId, 0, cancellationToken);

    public Task<JsonElement> GetModelCatalogAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/models/catalog", null, true, cancellationToken);

    public Task<ModelCatalogueResponse> GetTypedModelCatalogueAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<ModelCatalogueResponse>(
            HttpMethod.Get,
            "/v1/models/catalog",
            null,
            true,
            cancellationToken);

    public Task<ModelTaskListResponse> GetModelTasksAsync(CancellationToken cancellationToken = default) =>
        SendJsonAsync<ModelTaskListResponse>(
            HttpMethod.Get,
            "/v1/models/tasks",
            null,
            true,
            cancellationToken);

    public Task<ModelBenchmarkResponse> RecordModelBenchmarkAsync(
        string modelId,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<ModelBenchmarkRequest, ModelBenchmarkResponse>(
            "/v1/models/benchmark",
            new ModelBenchmarkRequest(
                RequireValue(modelId, nameof(modelId)),
                "manual_ui_benchmark",
                true,
                new Dictionary<string, string> { ["source"] = "models_page" }),
            cancellationToken);

    public Task<ModelImportResponse> ImportCivitaiModelAsync(
        string url,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<CivitaiImportRequest, ModelImportResponse>(
            "/v1/models/import/civitai",
            new CivitaiImportRequest(RequireValue(url, nameof(url))),
            cancellationToken);

    public Task<ModelImportResponse> ImportLocalModelAsync(
        string filePath,
        string folder,
        string? name = null,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<LocalModelImportRequest, ModelImportResponse>(
            "/v1/models/import/local",
            new LocalModelImportRequest(
                RequireValue(filePath, nameof(filePath)),
                RequireValue(folder, nameof(folder)),
                string.IsNullOrWhiteSpace(name) ? null : name.Trim()),
            cancellationToken);

    public Task<TensorRtMigrationStatus> GetTensorRtLegacyStatusAsync(
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<TensorRtMigrationStatus>(
            HttpMethod.Get,
            "/v1/models/tensorrt/legacy-status",
            null,
            true,
            cancellationToken);

    public Task<ModelTaskActionResponse> ImportLegacyTensorRtAsync(
        CancellationToken cancellationToken = default) =>
        SendJsonAsync<ModelTaskActionResponse>(
            HttpMethod.Post,
            "/v1/models/tensorrt/import-legacy",
            new StringContent("{}", Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    public Task<ModelTaskActionResponse> CancelLegacyTensorRtImportAsync(
        string taskId,
        CancellationToken cancellationToken = default) =>
        PostJsonAsync<TensorRtCancelImportRequest, ModelTaskActionResponse>(
            "/v1/models/tensorrt/cancel-import",
            new TensorRtCancelImportRequest(RequireValue(taskId, nameof(taskId))),
            cancellationToken);

    public Task<JsonElement> InstallModelAsync(string modelId, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/install",
            new JsonObject { ["model_id"] = RequireValue(modelId, nameof(modelId)) },
            cancellationToken);

    public Task<JsonElement> AcceptModelLicenseAsync(
        string modelId,
        string licenseId,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/accept",
            new JsonObject
            {
                ["model_id"] = RequireValue(modelId, nameof(modelId)),
                ["license_id"] = RequireValue(licenseId, nameof(licenseId))
            },
            cancellationToken);

    public Task<JsonElement> RestoreLocalModelAsync(string modelId, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/restore_local",
            new JsonObject { ["model_id"] = RequireValue(modelId, nameof(modelId)) },
            cancellationToken);

    public Task<JsonElement> InstallModelPackAsync(string packId, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/install_pack",
            new JsonObject { ["pack_id"] = RequireValue(packId, nameof(packId)) },
            cancellationToken);

    public Task<JsonElement> PromoteModelAsync(
        string modelId,
        string lane,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/promote",
            new JsonObject
            {
                ["model_id"] = RequireValue(modelId, nameof(modelId)),
                ["lane"] = RequireValue(lane, nameof(lane))
            },
            cancellationToken);

    public Task<JsonElement> RemoveUserModelAsync(string modelId, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/models/remove_user",
            new JsonObject { ["model_id"] = RequireValue(modelId, nameof(modelId)) },
            cancellationToken);

    public Task<JsonElement> GetHardwareAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/hardware", null, true, cancellationToken);

    public Task<JsonElement> GetSystemReadinessAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/system/readiness", null, true, cancellationToken);

    public Task<JsonElement> GetBaselineMetricsAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/metrics/baseline", null, true, cancellationToken);

    public Task<JsonElement> GetRenderProfilesAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/settings/render_profiles", null, true, cancellationToken);

    public Task<JsonElement> GetRenderProvidersAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/settings/render_providers", null, true, cancellationToken);

    public Task<JsonElement> SaveRenderProvidersAsync(
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/settings/render_providers", request, cancellationToken);

    public Task<JsonElement> SaveRenderProvidersAsync(
        JsonObject request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/settings/render_providers", request, cancellationToken);

    public Task<JsonElement> GetRenderRouteAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/render/route", null, true, cancellationToken);

    public Task<JsonElement> SaveRenderRouteAsync(
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/render/route/preferences", request, cancellationToken);

    public Task<JsonElement> GetTranscriptionSettingsAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/settings/transcription", null, true, cancellationToken);

    public Task<JsonElement> SaveTranscriptionSettingsAsync(
        JsonElement request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/settings/transcription", request, cancellationToken);

    public Task<JsonElement> SaveTranscriptionSettingsAsync(
        JsonObject request,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync("/v1/settings/transcription", request, cancellationToken);

    public Task<JsonElement> GetSecretStatusAsync(CancellationToken cancellationToken = default) =>
        SendJsonElementAsync(HttpMethod.Get, "/v1/settings/secrets/status", null, true, cancellationToken);

    public Task<JsonElement> SetSecretAsync(
        string key,
        string value,
        CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/settings/secrets/set",
            new JsonObject
            {
                ["name"] = RequireValue(key, nameof(key)),
                ["value"] = RequireValue(value, nameof(value))
            },
            cancellationToken);

    public Task<JsonElement> ClearSecretAsync(string key, CancellationToken cancellationToken = default) =>
        PostJsonElementAsync(
            "/v1/settings/secrets/clear",
            new JsonObject { ["name"] = RequireValue(key, nameof(key)) },
            cancellationToken);

    private Task<JsonElement> PostJsonElementAsync(
        string relativePath,
        JsonElement request,
        CancellationToken cancellationToken) =>
        SendJsonElementAsync(
            HttpMethod.Post,
            relativePath,
            new StringContent(request.GetRawText(), Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    private static void ValidateVariantReviewDecision(VariantReviewDecisionRequest request)
    {
        if (string.IsNullOrWhiteSpace(request.ArtifactPath) || request.ArtifactPath.Length > 1024)
        {
            throw new ArgumentException(
                "Artifact path must contain between 1 and 1024 characters.",
                nameof(request));
        }

        if (request.Decision is not ("approved" or "rejected" or "cherry_picked" or "unreviewed"))
        {
            throw new ArgumentException("Review decision is not supported.", nameof(request));
        }

        if (request.Notes?.Length > 2000)
        {
            throw new ArgumentException("Review notes cannot exceed 2000 characters.", nameof(request));
        }
    }

    private static void ValidateLiveCuePublish(LiveCuePublishRequest request)
    {
        if (request.OscHost.Length > 200)
        {
            throw new ArgumentException("OSC host cannot exceed 200 characters.", nameof(request));
        }

        if (request.OscPort is < 1 or > 65535)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "OSC port must be between 1 and 65535.");
        }

        if (!double.IsFinite(request.PlaybackSpeed) || request.PlaybackSpeed is <= 0 or > 8)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "Playback speed must be greater than 0 and at most 8.");
        }
    }

    private static void ValidateWorldAdapterExport(WorldAdapterExportRequest request)
    {
        if (request.Adapter is not ("touchdesigner" or "unreal"))
        {
            throw new ArgumentException("World adapter must be touchdesigner or unreal.", nameof(request));
        }

        if (request.VariantIndex < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "Variant index cannot be negative.");
        }

        if (request.SequenceName?.Length > 200)
        {
            throw new ArgumentException("Sequence name cannot exceed 200 characters.", nameof(request));
        }
    }

    private static void ValidateUnrealBundleExport(UnrealBundleExportRequest request)
    {
        if (request.VariantIndex < 0)
        {
            throw new ArgumentOutOfRangeException(nameof(request), "Variant index cannot be negative.");
        }

        if (request.BundleName?.Length > 120)
        {
            throw new ArgumentException("Bundle name cannot exceed 120 characters.", nameof(request));
        }
    }

    private static void ValidateUnrealImportPlan(UnrealImportPlanRequest request)
    {
        ValidateUnrealPath(request.BundleDirectory, "Bundle directory", nameof(request), required: true);
        ValidateUnrealPath(request.ContentPath, "Content path", nameof(request), required: false);
        if (request.AssetName?.Length > 120)
        {
            throw new ArgumentException("Asset name cannot exceed 120 characters.", nameof(request));
        }
    }

    private static void ValidateUnrealReturnImport(UnrealReturnImportRequest request)
    {
        ValidateUnrealPath(request.BundleDirectory, "Bundle directory", nameof(request), required: true);
        ValidateUnrealPath(request.SourceDirectory, "Source directory", nameof(request), required: false);
    }

    private static void ValidateUnrealPath(
        string? value,
        string label,
        string parameterName,
        bool required)
    {
        if (required && string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException($"{label} must contain at least one character.", parameterName);
        }

        if (value?.Length > 260)
        {
            throw new ArgumentException($"{label} cannot exceed 260 characters.", parameterName);
        }
    }

    private Task<JsonElement> PostJsonElementAsync(
        string relativePath,
        JsonObject request,
        CancellationToken cancellationToken) =>
        SendJsonElementAsync(
            HttpMethod.Post,
            relativePath,
            new StringContent(request.ToJsonString(StudioJson.Options), Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    private Task<JsonElement> PostJsonElementAsync<T>(
        string relativePath,
        T request,
        JsonTypeInfo<T> typeInfo,
        CancellationToken cancellationToken) =>
        SendJsonElementAsync(
            HttpMethod.Post,
            relativePath,
            JsonContent.Create(request, typeInfo),
            true,
            cancellationToken);

    private Task<TResponse> PostJsonAsync<TRequest, TResponse>(
        string relativePath,
        TRequest request,
        CancellationToken cancellationToken) =>
        SendJsonAsync<TResponse>(
            HttpMethod.Post,
            relativePath,
            JsonContent.Create(request, StudioJson.GetTypeInfo<TRequest>()),
            true,
            cancellationToken);

    private Task<SetupTaskActionResponse> PostEmptySetupActionAsync(
        string relativePath,
        CancellationToken cancellationToken) =>
        SendJsonAsync<SetupTaskActionResponse>(
            HttpMethod.Post,
            relativePath,
            null,
            true,
            cancellationToken);

    private Task<SetupTaskActionResponse> PostSetupActionAsync<TRequest>(
        string relativePath,
        TRequest request,
        JsonTypeInfo<TRequest> typeInfo,
        CancellationToken cancellationToken) =>
        SendJsonAsync<SetupTaskActionResponse>(
            HttpMethod.Post,
            relativePath,
            JsonContent.Create(request, typeInfo),
            true,
            cancellationToken);

    private Task<StudioJobActionResponse> PostJobActionAsync(
        string projectId,
        string jobId,
        string action,
        CancellationToken cancellationToken) =>
        SendJsonAsync<StudioJobActionResponse>(
            HttpMethod.Post,
            $"/v1/projects/{EscapeIdentifier(projectId)}/jobs/{EscapeIdentifier(jobId)}/{action}",
            new StringContent("{}", Encoding.UTF8, "application/json"),
            true,
            cancellationToken);

    private Task<JsonElement> PostProjectJobJsonAsync(
        string projectId,
        string jobId,
        string action,
        CancellationToken cancellationToken) =>
        PostJsonElementAsync(
            $"/v1/projects/{EscapeIdentifier(projectId)}/jobs/{EscapeIdentifier(jobId)}/{action}",
            new JsonObject(),
            cancellationToken);

    private Task<JsonElement> GetProjectJobJsonAsync(
        string projectId,
        string jobId,
        string suffix,
        CancellationToken cancellationToken) =>
        SendJsonElementAsync(
            HttpMethod.Get,
            $"/v1/projects/{EscapeIdentifier(projectId)}/jobs/{EscapeIdentifier(jobId)}/{suffix}",
            null,
            true,
            cancellationToken);

    private async Task<T> SendJsonAsync<T>(
        HttpMethod method,
        string relativePath,
        HttpContent? content,
        bool includeCredentials,
        CancellationToken cancellationToken)
    {
        using var request = await CreateRequestAsync(method, relativePath, content, includeCredentials, cancellationToken)
            .ConfigureAwait(false);
        using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);

        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var result = await JsonSerializer.DeserializeAsync(
                stream,
                StudioJson.GetTypeInfo<T>(),
                cancellationToken)
            .ConfigureAwait(false);
        return result ?? throw new StudioApiException(
            response.StatusCode,
            "EMPTY_RESPONSE",
            "Studio returned an empty response.",
            "Retry the operation and review the backend logs if it continues.");
    }

    private async Task<JsonElement> SendJsonElementAsync(
        HttpMethod method,
        string relativePath,
        HttpContent? content,
        bool includeCredentials,
        CancellationToken cancellationToken)
    {
        using var request = await CreateRequestAsync(method, relativePath, content, includeCredentials, cancellationToken)
            .ConfigureAwait(false);
        using var response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        await using var stream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var document = await JsonDocument.ParseAsync(stream, cancellationToken: cancellationToken).ConfigureAwait(false);
        return document.RootElement.Clone();
    }

    private async Task<JsonElement> UploadProjectAssetAsync(
        string projectId,
        string assetRoute,
        Stream content,
        string fileName,
        string? contentType,
        CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(content);
        if (!content.CanRead)
        {
            throw new ArgumentException("The selected asset stream is not readable.", nameof(content));
        }

        var safeFileName = Path.GetFileName(fileName);
        if (string.IsNullOrWhiteSpace(safeFileName))
        {
            throw new ArgumentException("The selected asset must have a file name.", nameof(fileName));
        }

        using var multipart = new MultipartFormDataContent();
        using var streamContent = new StreamContent(content);
        streamContent.Headers.ContentType = new MediaTypeHeaderValue(
            string.IsNullOrWhiteSpace(contentType) ? "application/octet-stream" : contentType);
        multipart.Add(streamContent, "file", safeFileName);

        using var request = await CreateRequestAsync(
                HttpMethod.Post,
                $"/v1/projects/{EscapeIdentifier(projectId)}/assets/{assetRoute}",
                multipart,
                true,
                cancellationToken)
            .ConfigureAwait(false);
        using var response = await _httpClient.SendAsync(
                request,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken)
            .ConfigureAwait(false);
        await EnsureSuccessAsync(response, cancellationToken).ConfigureAwait(false);
        await using var responseStream = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        using var document = await JsonDocument.ParseAsync(
                responseStream,
                cancellationToken: cancellationToken)
            .ConfigureAwait(false);
        return document.RootElement.Clone();
    }

    private static string BuildQuery(params (string Name, string? Value)[] values)
    {
        var encoded = values
            .Where(static value => value.Value is not null)
            .Select(static value =>
                $"{Uri.EscapeDataString(value.Name)}={Uri.EscapeDataString(value.Value!)}");
        var query = string.Join('&', encoded);
        return query.Length == 0 ? string.Empty : $"?{query}";
    }

    private static string Invariant(int value) => value.ToString(CultureInfo.InvariantCulture);

    private static string Invariant(long value) => value.ToString(CultureInfo.InvariantCulture);

    private static string Invariant(double value) => value.ToString(CultureInfo.InvariantCulture);

    private SignedMediaUrlRequest NormalizeSignedMediaUrlRequest(SignedMediaUrlRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        string purpose = RequireValue(request.Purpose, nameof(request.Purpose)).ToLowerInvariant();
        if (purpose is not ("file" or "audio" or "preview"))
        {
            throw new ArgumentException("Signed media purpose must be file, audio, or preview.", nameof(request));
        }

        string? path = string.IsNullOrWhiteSpace(request.Path) ? null : request.Path.Trim();
        if (purpose is "file" or "audio")
        {
            path = RequireValue(path, nameof(request.Path));
        }

        if (request.Query.ValueKind is not (JsonValueKind.Undefined or JsonValueKind.Null or JsonValueKind.Object))
        {
            throw new ArgumentException("Signed media query payloads must be JSON objects when provided.", nameof(request));
        }

        return new SignedMediaUrlRequest
        {
            Purpose = purpose,
            Path = path,
            Query = request.Query.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null
                ? default
                : request.Query.Clone()
        };
    }

    private Uri ResolveStreamTarget(string target)
    {
        string normalizedTarget = RequireValue(target, nameof(target));
        if (Uri.TryCreate(normalizedTarget, UriKind.Absolute, out Uri? absoluteTarget))
        {
            if (!absoluteTarget.Scheme.Equals(Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase) &&
                !absoluteTarget.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidOperationException("Studio signed media URLs must use HTTP or HTTPS.");
            }

            return absoluteTarget;
        }

        return new Uri(_endpointProvider.CurrentBackendUri, normalizedTarget);
    }

    internal static bool ShouldFallbackToLegacyProjectFileRoute(StudioApiException exception)
        => exception.StatusCode is HttpStatusCode.NotFound
            or HttpStatusCode.MethodNotAllowed
            or HttpStatusCode.NotImplemented;

    private async Task<HttpRequestMessage> CreateRequestAsync(
        HttpMethod method,
        string relativePath,
        HttpContent? content,
        bool includeCredentials,
        CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(relativePath) ||
            Uri.TryCreate(relativePath, UriKind.Absolute, out _))
        {
            throw new ArgumentException("Studio API requests must use a relative path.", nameof(relativePath));
        }

        var baseUri = _endpointProvider.CurrentBackendUri;
        var target = new Uri(baseUri, relativePath.TrimStart('/'));
        if (!SameOrigin(baseUri, target))
        {
            throw new InvalidOperationException("Refusing to send a Studio API request to a different origin.");
        }

        var request = new HttpRequestMessage(method, target) { Content = content };
        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (includeCredentials)
        {
            var token = await _tokenProvider.GetTokenAsync(cancellationToken).ConfigureAwait(false);
            if (!string.IsNullOrWhiteSpace(token))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            }
        }

        return request;
    }

    internal static async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return;
        }

        string body;
        try
        {
            body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        }
        catch
        {
            body = string.Empty;
        }

        var error = ParseError(body);
        if (response.StatusCode == HttpStatusCode.Conflict &&
            string.Equals(
                error.Code,
                ProjectRevisionConflictException.ErrorCode,
                StringComparison.OrdinalIgnoreCase))
        {
            throw new ProjectRevisionConflictException(
                error.Message,
                error.Hint,
                error.ProjectId,
                error.ExpectedRevision,
                error.ActualRevision,
                response.Headers.WwwAuthenticate.Any());
        }

        throw new StudioApiException(
            response.StatusCode,
            error.Code,
            error.Message,
            error.Hint,
            response.Headers.WwwAuthenticate.Any());
    }

    private static ApiError ParseError(string body)
    {
        try
        {
            using var document = JsonDocument.Parse(body);
            var root = document.RootElement;
            if (root.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.Object)
            {
                JsonElement details = error.TryGetProperty("details", out var nestedDetails) &&
                                      nestedDetails.ValueKind == JsonValueKind.Object
                    ? nestedDetails
                    : default;
                return new ApiError(
                    ReadString(error, "code") ?? "HTTP_ERROR",
                    ReadString(error, "message") ?? "Studio request failed.",
                    ReadString(error, "hint") ?? "Review the request and backend status, then retry.",
                    ReadString(error, "project_id") ?? ReadString(details, "project_id"),
                    ReadInt64(error, "expected_revision") ?? ReadInt64(details, "expected_revision"),
                    ReadInt64(error, "actual_revision") ??
                    ReadInt64(error, "current_revision") ??
                    ReadInt64(details, "actual_revision") ??
                    ReadInt64(details, "current_revision"));
            }

            if (root.TryGetProperty("detail", out var detail))
            {
                var message = detail.ValueKind == JsonValueKind.String
                    ? detail.GetString()
                    : detail.GetRawText();
                return new ApiError(
                    "VALIDATION_ERROR",
                    message ?? "Studio rejected the request.",
                    "Check the highlighted values and retry.");
            }
        }
        catch
        {
        }

        return new ApiError("HTTP_ERROR", "Studio request failed.", "Check the backend connection and retry.");
    }

    private static string EscapeIdentifier(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("A project ID is required.", nameof(value));
        }

        return Uri.EscapeDataString(value.Trim());
    }

    private static string RequireValue(string value, string parameterName)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new ArgumentException("A value is required.", parameterName);
        }

        return value.Trim();
    }

    private static string RequireShortValue(string value, string parameterName, int maximumLength)
    {
        var result = RequireValue(value, parameterName);
        if (result.Length > maximumLength)
        {
            throw new ArgumentException($"The value cannot exceed {maximumLength} characters.", parameterName);
        }

        return result;
    }

    private static string ValidateSetupProfile(string value)
    {
        var profile = RequireShortValue(value, nameof(value), 20).ToLowerInvariant();
        if (profile is not ("cpu" or "cuda" or "directml"))
        {
            throw new ArgumentException("Setup profile must be cpu, cuda, or directml.", nameof(value));
        }

        return profile;
    }

    private static string ValidateComfyUiFlavor(string value, bool allowAuto)
    {
        var flavor = RequireShortValue(value, nameof(value), 20).ToLowerInvariant();
        if (flavor is not ("cpu" or "nvidia" or "amd") && !(allowAuto && flavor == "auto"))
        {
            throw new ArgumentException(
                allowAuto
                    ? "ComfyUI flavor must be auto, cpu, nvidia, or amd."
                    : "ComfyUI flavor must be cpu, nvidia, or amd.",
                nameof(value));
        }

        return flavor;
    }

    private static void ValidatePort(int port, string parameterName)
    {
        if (port is < 1 or > 65535)
        {
            throw new ArgumentOutOfRangeException(parameterName, "Port must be between 1 and 65535.");
        }
    }

    private static TimelineRenderRequest NormalizeTimelineRenderRequest(TimelineRenderRequest request)
    {
        ValidateTimelineDimension(request.Width, nameof(request.Width));
        ValidateTimelineDimension(request.Height, nameof(request.Height));
        if (!double.IsFinite(request.Fps) || request.Fps is < 1 or > 120)
        {
            throw new ArgumentOutOfRangeException(nameof(request.Fps), "Timeline FPS must be between 1 and 120.");
        }

        var videoCodec = RequireValue(request.VideoCodec, nameof(request.VideoCodec)).ToLowerInvariant();
        if (videoCodec is not ("h264" or "hevc" or "prores"))
        {
            throw new ArgumentException("Timeline video codec must be h264, hevc, or prores.", nameof(request.VideoCodec));
        }

        var audioCodec = RequireValue(request.AudioCodec, nameof(request.AudioCodec)).ToLowerInvariant();
        if (audioCodec is not ("aac" or "pcm_s16le"))
        {
            throw new ArgumentException("Timeline audio codec must be aac or pcm_s16le.", nameof(request.AudioCodec));
        }

        if ((videoCodec == "prores") != (audioCodec == "pcm_s16le"))
        {
            throw new ArgumentException(
                "ProRes requires pcm_s16le audio; H.264 and HEVC require AAC audio.",
                nameof(request.AudioCodec));
        }

        if (request.Quality is < 1 or > 51)
        {
            throw new ArgumentOutOfRangeException(
                nameof(request.Quality));
        }

        var name = RequireValue(request.Name, nameof(request.Name));
        if (name.Length > 128 ||
            name.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
            name.Contains('/') ||
            name.Contains('\\'))
        {
            throw new ArgumentException("Timeline output name must be a valid file name of at most 128 characters.", nameof(request.Name));
        }

        return request with
        {
            VideoCodec = videoCodec,
            AudioCodec = audioCodec,
            Name = name
        };
    }

    private static void ValidateTimelineDimension(int value, string parameterName)
    {
        if (value is < 256 or > 7680 || value % 2 != 0)
        {
            throw new ArgumentOutOfRangeException(parameterName, "Timeline dimensions must be even and between 256 and 7680.");
        }
    }

    private static bool SameOrigin(Uri left, Uri right) =>
        string.Equals(left.Scheme, right.Scheme, StringComparison.OrdinalIgnoreCase) &&
        string.Equals(left.Host, right.Host, StringComparison.OrdinalIgnoreCase) &&
        left.Port == right.Port;

    private static string? ReadString(JsonElement parent, string name) =>
        parent.ValueKind == JsonValueKind.Object &&
        parent.TryGetProperty(name, out var value) &&
        value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static long? ReadInt64(JsonElement parent, string name)
    {
        if (parent.ValueKind != JsonValueKind.Object ||
            !parent.TryGetProperty(name, out var value))
        {
            return null;
        }

        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out long number))
        {
            return number;
        }

        return value.ValueKind == JsonValueKind.String &&
               long.TryParse(value.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out number)
            ? number
            : null;
    }

    private sealed record ApiError(
        string Code,
        string Message,
        string Hint,
        string? ProjectId = null,
        long? ExpectedRevision = null,
        long? ActualRevision = null);

    public void Dispose()
    {
        if (_ownsClient)
        {
            _httpClient.Dispose();
        }
    }
}

public class StudioApiException : Exception
{
    public StudioApiException(
        HttpStatusCode statusCode,
        string code,
        string message,
        string hint,
        bool authenticationChallenge = false)
        : base(message)
    {
        StatusCode = statusCode;
        Code = code;
        Hint = hint;
        AuthenticationChallenge = authenticationChallenge;
    }

    public HttpStatusCode StatusCode { get; }
    public string Code { get; }
    public string Hint { get; }
    public bool AuthenticationChallenge { get; }

    public string UserFacingMessage => string.IsNullOrWhiteSpace(Hint) ? Message : $"{Message} {Hint}";
}

public sealed class ProjectRevisionConflictException : StudioApiException
{
    public const string ErrorCode = "PROJECT_REVISION_CONFLICT";

    public ProjectRevisionConflictException(
        string message,
        string hint,
        string? projectId,
        long? expectedRevision,
        long? actualRevision,
        bool authenticationChallenge = false)
        : base(HttpStatusCode.Conflict, ErrorCode, message, hint, authenticationChallenge)
    {
        ProjectId = projectId;
        ExpectedRevision = expectedRevision;
        ActualRevision = actualRevision;
    }

    public string? ProjectId { get; }

    public long? ExpectedRevision { get; }

    public long? ActualRevision { get; }
}
