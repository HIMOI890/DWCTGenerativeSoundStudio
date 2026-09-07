using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace EdmgStudio.Core.Models;

public sealed record HealthResponse(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("version")] string Version);

public sealed class ProjectListResponse
{
    [JsonPropertyName("projects")]
    public List<ProjectDto> Projects { get; init; } = [];
}

public sealed class ProjectResponse
{
    [JsonPropertyName("project")]
    public required ProjectDto Project { get; init; }

    [JsonPropertyName("visual_dna")]
    public JsonElement VisualDna { get; init; }

    [JsonPropertyName("visual_dna_hints")]
    public JsonElement VisualDnaHints { get; init; }
}

public sealed class ProjectDto
{
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    [JsonPropertyName("created_at")]
    public string CreatedAt { get; init; } = string.Empty;

    [JsonPropertyName("updated_at")]
    public string UpdatedAt { get; init; } = string.Empty;

    [JsonPropertyName("revision")]
    public long Revision { get; init; }

    [JsonPropertyName("meta")]
    public JsonElement Meta { get; init; }

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonIgnore]
    public bool HasAudio => TryGetMetaObject("audio", out _);

    [JsonIgnore]
    public bool HasAnalysis => TryGetMetaObject("analysis", out _);

    [JsonIgnore]
    public bool HasPlan => TryGetMetaObject("last_plan", out _);

    [JsonIgnore]
    public string AudioFileName =>
        TryGetMetaObject("audio", out var audio) && audio.TryGetProperty("filename", out var filename)
            ? filename.GetString() ?? string.Empty
            : string.Empty;

    [JsonIgnore]
    public long? AudioSizeBytes =>
        TryGetMetaObject("audio", out var audio) && audio.TryGetProperty("size_bytes", out var size) && size.TryGetInt64(out var value)
            ? value
            : null;

    [JsonIgnore]
    public double? Bpm =>
        GetAnalysisNumber("bpm") ??
        GetAnalysisNumber("tempo_bpm") ??
        GetAnalysisNumber("tempo");

    [JsonIgnore]
    public double? DurationSeconds =>
        GetAnalysisNumber("duration_s") ??
        GetAnalysisNumber("duration") ??
        GetTopLevelAnalysisNumber("duration_s") ??
        GetTopLevelAnalysisNumber("duration");

    [JsonIgnore]
    public int SectionCount =>
        TryGetMetaObject("analysis", out var analysis) &&
        analysis.TryGetProperty("sections", out var sections) &&
        sections.ValueKind == JsonValueKind.Array
            ? sections.GetArrayLength()
            : 0;

    [JsonIgnore]
    public string TranscriptStatus
    {
        get
        {
            if (!TryGetMetaObject("analysis", out var analysis) ||
                !analysis.TryGetProperty("transcript", out var transcript) ||
                transcript.ValueKind is not (JsonValueKind.Object or JsonValueKind.String))
            {
                return "Waiting for analysis";
            }

            if (transcript.ValueKind == JsonValueKind.String)
            {
                return string.IsNullOrWhiteSpace(transcript.GetString()) ? "Audio-only analysis" : "Transcript ready";
            }

            if (transcript.TryGetProperty("text", out var text) && !string.IsNullOrWhiteSpace(text.GetString()))
            {
                return "Transcript ready";
            }

            if (transcript.TryGetProperty("note", out var note) && !string.IsNullOrWhiteSpace(note.GetString()))
            {
                return note.GetString()!;
            }

            if (transcript.TryGetProperty("error", out var error) && !string.IsNullOrWhiteSpace(error.GetString()))
            {
                return "Transcription failed; audio analysis is still available";
            }

            return HasAnalysis ? "Audio-only analysis" : "Waiting for analysis";
        }
    }

    [JsonIgnore]
    public IReadOnlyList<PlanVariantDto> PlanVariants
    {
        get
        {
            if (!TryGetMetaObject("last_plan", out var plan) ||
                !plan.TryGetProperty("variants", out var variants) ||
                variants.ValueKind != JsonValueKind.Array)
            {
                return [];
            }

            return JsonSerializer.Deserialize(
                variants.GetRawText(),
                StudioJson.GetTypeInfo<List<PlanVariantDto>>()) ?? [];
        }
    }

    private bool TryGetMetaObject(string propertyName, out JsonElement value)
    {
        value = default;
        return Meta.ValueKind == JsonValueKind.Object &&
               Meta.TryGetProperty(propertyName, out value) &&
               value.ValueKind == JsonValueKind.Object &&
               value.EnumerateObject().Any();
    }

    private double? GetAnalysisNumber(string propertyName)
    {
        if (!TryGetMetaObject("analysis", out var analysis) ||
            !analysis.TryGetProperty("features", out var features) ||
            features.ValueKind != JsonValueKind.Object ||
            !features.TryGetProperty(propertyName, out var value) ||
            !value.TryGetDouble(out var number))
        {
            return null;
        }

        return number;
    }

    private double? GetTopLevelAnalysisNumber(string propertyName)
    {
        return TryGetMetaObject("analysis", out var analysis) &&
               analysis.TryGetProperty(propertyName, out var value) &&
               value.TryGetDouble(out var number)
            ? number
            : null;
    }
}

public sealed record CreateProjectRequest(
    [property: JsonPropertyName("name")] string Name);

public sealed record PlanRequest(
    [property: JsonPropertyName("title")] string? Title,
    [property: JsonPropertyName("user_notes")] string? UserNotes,
    [property: JsonPropertyName("style_prefs")] string? StylePreferences,
    [property: JsonPropertyName("num_variants")] int NumberOfVariants = 3,
    [property: JsonPropertyName("max_scenes")] int MaximumScenes = 12,
    [property: JsonPropertyName("expected_revision")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    long? ExpectedRevision = null);

public sealed class AnalysisResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("analysis")]
    public JsonElement Analysis { get; init; }
}

public sealed class SignedMediaUrlBatchRequest
{
    [JsonPropertyName("requests")]
    public List<SignedMediaUrlRequest> Requests { get; init; } = [];
}

public sealed class SignedMediaUrlRequest
{
    [JsonPropertyName("purpose")]
    public string Purpose { get; init; } = string.Empty;

    [JsonPropertyName("path")]
    public string? Path { get; init; }

    [JsonPropertyName("query")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingDefault)]
    public JsonElement Query { get; init; }
}

public sealed class SignedMediaUrlBatchResponse
{
    [JsonPropertyName("expires_at")]
    public long ExpiresAtUnixSeconds { get; init; }

    [JsonPropertyName("urls")]
    public List<SignedMediaUrlResponse> Urls { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class SignedMediaUrlResponse
{
    [JsonPropertyName("purpose")]
    public string Purpose { get; init; } = string.Empty;

    [JsonPropertyName("url")]
    public string Url { get; init; } = string.Empty;

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class PlanDto
{
    [JsonPropertyName("source")]
    public string Source { get; init; } = string.Empty;

    [JsonPropertyName("duration_s")]
    public double? DurationSeconds { get; init; }

    [JsonPropertyName("variants")]
    public List<PlanVariantDto> Variants { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class PlanVariantDto
{
    [JsonPropertyName("index")]
    public int? Index { get; init; }

    [JsonPropertyName("name")]
    public string? Name { get; init; }

    [JsonPropertyName("logline")]
    public string? Logline { get; init; }

    [JsonPropertyName("duration_s")]
    public double? DurationSeconds { get; init; }

    [JsonPropertyName("scenes")]
    public List<PlanSceneDto> Scenes { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }

    [JsonIgnore]
    public string DisplayName => !string.IsNullOrWhiteSpace(Name) ? Name! : $"Variant {(Index ?? 0) + 1}";

    [JsonIgnore]
    public int SceneCount => Scenes.Count;
}

public sealed class PlanSceneDto
{
    [JsonPropertyName("start_s")]
    public double StartSeconds { get; init; }

    [JsonPropertyName("end_s")]
    public double EndSeconds { get; init; }

    [JsonPropertyName("prompt")]
    public string Prompt { get; init; } = string.Empty;

    [JsonPropertyName("negative_prompt")]
    public string? NegativePrompt { get; init; }

    [JsonPropertyName("setting")]
    public string? Setting { get; init; }

    [JsonPropertyName("shot_type")]
    public string? ShotType { get; init; }

    [JsonPropertyName("character_lock")]
    public string? CharacterLock { get; init; }

    [JsonPropertyName("style_lock")]
    public string? StyleLock { get; init; }

    [JsonPropertyName("start_state")]
    public string? StartState { get; init; }

    [JsonPropertyName("end_state")]
    public string? EndState { get; init; }

    [JsonPropertyName("subject")]
    public string? Subject { get; init; }

    [JsonPropertyName("action")]
    public string? Action { get; init; }

    [JsonPropertyName("camera")]
    public string? Camera { get; init; }

    [JsonPropertyName("motion")]
    public string? Motion { get; init; }

    [JsonPropertyName("environment_motion")]
    public string? EnvironmentMotion { get; init; }

    [JsonPropertyName("continuity_note")]
    public string? ContinuityNote { get; init; }

    [JsonIgnore]
    public string? ContinuityInstruction
    {
        get
        {
            if (!string.IsNullOrWhiteSpace(ContinuityNote))
            {
                return ContinuityNote;
            }

            return AdditionalData?.TryGetValue("continuity", out JsonElement value) == true
                && value.ValueKind == JsonValueKind.String
                    ? value.GetString()
                    : null;
        }
    }

    [JsonPropertyName("transition")]
    public string? Transition { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed record StudioJobListResponse(
    [property: JsonPropertyName("jobs")] IReadOnlyList<StudioJob> Jobs);

public sealed record StudioJobActionResponse(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("job")] StudioJob Job);

public sealed record StudioJob(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("project_id")] string ProjectId,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("created_at")] string? CreatedAt,
    [property: JsonPropertyName("updated_at")] string? UpdatedAt,
    [property: JsonPropertyName("started_at")] string? StartedAt,
    [property: JsonPropertyName("finished_at")] string? FinishedAt,
    [property: JsonPropertyName("error")] string? Error,
    [property: JsonPropertyName("progress")] StudioJobProgress? Progress,
    [property: JsonPropertyName("result")] JsonElement? Result,
    [property: JsonPropertyName("payload")] JsonElement? Payload,
    [property: JsonPropertyName("attempt")] int Attempt = 0)
{
    public bool IsActive => Status is "queued" or "paused" or "running";

    public bool CanPause => Status == "queued";

    public bool CanResume => Status == "paused";

    public bool CanCancel => IsActive;

    public bool CanRetry => Status is "succeeded" or "failed" or "canceled";
}

public sealed record StudioJobProgress(
    [property: JsonPropertyName("percent")] double? Percent,
    [property: JsonPropertyName("stage")] string? Stage,
    [property: JsonPropertyName("message")] string? Message,
    [property: JsonPropertyName("current")] double? Current,
    [property: JsonPropertyName("total")] double? Total);

public sealed record TimelineUpdateRequest(
    [property: JsonPropertyName("timeline")] JsonElement Timeline,
    [property: JsonPropertyName("expected_revision")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    long? ExpectedRevision = null);

public sealed record TimelineAutosaveRequest(
    [property: JsonPropertyName("timeline")] JsonElement Timeline,
    [property: JsonPropertyName("meta")] JsonElement? Metadata = null,
    [property: JsonPropertyName("reason")] string? Reason = null,
    [property: JsonPropertyName("expected_revision")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    long? ExpectedRevision = null);

public sealed record TimelineRenderRequest(
    [property: JsonPropertyName("width")] int Width,
    [property: JsonPropertyName("height")] int Height,
    [property: JsonPropertyName("fps")] double Fps,
    [property: JsonPropertyName("video_codec")] string VideoCodec,
    [property: JsonPropertyName("audio_codec")] string AudioCodec,
    [property: JsonPropertyName("quality")] int Quality,
    [property: JsonPropertyName("name")] string Name);

public sealed record TimelineRenderResponse(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("job")] StudioJob Job);

public sealed record MotionPhraseRequest(
    [property: JsonPropertyName("phrase")] string Phrase,
    [property: JsonPropertyName("start_s")] double StartSeconds,
    [property: JsonPropertyName("end_s")] double EndSeconds,
    [property: JsonPropertyName("overrides")] IReadOnlyDictionary<string, double>? Overrides = null);

public sealed record ApplyMotionGrammarRequest(
    [property: JsonPropertyName("phrases")] IReadOnlyList<MotionPhraseRequest> Phrases,
    [property: JsonPropertyName("overwrite_motion_track")] bool OverwriteMotionTrack = false,
    [property: JsonPropertyName("expected_revision")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    long? ExpectedRevision = null);

public sealed record ApplyMotionGrammarResponse(
    [property: JsonPropertyName("ok")] bool Ok,
    [property: JsonPropertyName("timeline")] JsonElement Timeline);

public sealed record RecoveryApplyRequest(
    [property: JsonPropertyName("source")] string Source = "journal",
    [property: JsonPropertyName("snapshot_name")] string? SnapshotName = null,
    [property: JsonPropertyName("expected_revision")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    long? ExpectedRevision = null);

public sealed class VariantReviewDecisionRequest
{
    [JsonPropertyName("artifact_path")]
    public required string ArtifactPath { get; init; }

    [JsonPropertyName("decision")]
    public required string Decision { get; init; }

    [JsonPropertyName("notes")]
    public string? Notes { get; init; }

    [JsonPropertyName("cherry_pick_traits")]
    public IReadOnlyList<string> CherryPickTraits { get; init; } = [];

    [JsonPropertyName("lock_fields")]
    public IReadOnlyList<string> LockFields { get; init; } = [];
}

public sealed class VariantReviewDecisionResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("variant_review")]
    public JsonElement VariantReview { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class LiveCuePublishRequest
{
    [JsonPropertyName("osc_host")]
    public string OscHost { get; init; } = "127.0.0.1";

    [JsonPropertyName("osc_port")]
    public int OscPort { get; init; } = 9000;

    [JsonPropertyName("midi_enabled")]
    public bool MidiEnabled { get; init; } = true;

    [JsonPropertyName("websocket_enabled")]
    public bool WebsocketEnabled { get; init; } = true;

    [JsonPropertyName("playback_speed")]
    public double PlaybackSpeed { get; init; } = 1.0;
}

public sealed class LiveCuePublishResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("publish")]
    public JsonElement Publish { get; init; }

    [JsonPropertyName("event_count")]
    public int? EventCount { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class WorldAdapterExportRequest
{
    [JsonPropertyName("adapter")]
    public string Adapter { get; init; } = "touchdesigner";

    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("sequence_name")]
    public string? SequenceName { get; init; }
}

public sealed class WorldAdapterExportResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("adapter")]
    public string Adapter { get; init; } = string.Empty;

    [JsonPropertyName("payload")]
    public JsonElement Payload { get; init; }

    [JsonPropertyName("simulation")]
    public JsonElement Simulation { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealBundleExportRequest
{
    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("bundle_name")]
    public string? BundleName { get; init; }

    [JsonPropertyName("include_zip")]
    public bool IncludeZip { get; init; } = true;
}

public sealed class UnrealBundleDto
{
    [JsonPropertyName("bundle_dir")]
    public string BundleDirectory { get; init; } = string.Empty;

    [JsonPropertyName("manifest_path")]
    public string ManifestPath { get; init; } = string.Empty;

    [JsonPropertyName("zip_path")]
    public string? ZipPath { get; init; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; init; }

    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("sequence_name")]
    public string? SequenceName { get; init; }

    [JsonPropertyName("files")]
    public List<string> Files { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealBundleExportResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("bundle")]
    public UnrealBundleDto Bundle { get; init; } = new();

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealImportPlanRequest
{
    [JsonPropertyName("bundle_dir")]
    public string BundleDirectory { get; init; } = string.Empty;

    [JsonPropertyName("content_path")]
    public string? ContentPath { get; init; }

    [JsonPropertyName("asset_name")]
    public string? AssetName { get; init; }
}

public sealed class UnrealImportPlanResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("plan_path")]
    public string PlanPath { get; init; } = string.Empty;

    [JsonPropertyName("plan")]
    public JsonElement Plan { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealReturnImportRequest
{
    [JsonPropertyName("bundle_dir")]
    public string BundleDirectory { get; init; } = string.Empty;

    [JsonPropertyName("source_dir")]
    public string? SourceDirectory { get; init; }
}

public sealed class UnrealReturnedMediaDto
{
    [JsonPropertyName("kind")]
    public string Kind { get; init; } = string.Empty;

    [JsonPropertyName("path")]
    public string Path { get; init; } = string.Empty;

    [JsonPropertyName("source_path")]
    public string SourcePath { get; init; } = string.Empty;

    [JsonPropertyName("metadata_path")]
    public string MetadataPath { get; init; } = string.Empty;

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealReturnImportDto
{
    [JsonPropertyName("bundle_dir")]
    public string BundleDirectory { get; init; } = string.Empty;

    [JsonPropertyName("source_dir")]
    public string SourceDirectory { get; init; } = string.Empty;

    [JsonPropertyName("manifest_path")]
    public string? ManifestPath { get; init; }

    [JsonPropertyName("return_contract_path")]
    public string? ReturnContractPath { get; init; }

    [JsonPropertyName("created_at")]
    public string? CreatedAt { get; init; }

    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("sequence_name")]
    public string? SequenceName { get; init; }

    [JsonPropertyName("media")]
    public List<UnrealReturnedMediaDto> Media { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class UnrealReturnImportResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("imported")]
    public UnrealReturnImportDto Imported { get; init; } = new();

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public static class StudioJson
{
    public static readonly JsonSerializerOptions Options = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    public static readonly StudioJsonContext Context = new(Options);

    public static JsonTypeInfo<T> GetTypeInfo<T>()
        => Context.GetTypeInfo(typeof(T)) as JsonTypeInfo<T>
           ?? throw new InvalidOperationException(
               $"JSON metadata is not registered for {typeof(T).FullName}.");
}

[JsonSourceGenerationOptions(GenerationMode = JsonSourceGenerationMode.Metadata)]
[JsonSerializable(typeof(HealthResponse))]
[JsonSerializable(typeof(ProjectListResponse))]
[JsonSerializable(typeof(ProjectResponse))]
[JsonSerializable(typeof(CreateProjectRequest))]
[JsonSerializable(typeof(AnalysisResponse))]
[JsonSerializable(typeof(SignedMediaUrlBatchRequest))]
[JsonSerializable(typeof(SignedMediaUrlRequest))]
[JsonSerializable(typeof(SignedMediaUrlBatchResponse))]
[JsonSerializable(typeof(SignedMediaUrlResponse))]
[JsonSerializable(typeof(PlanRequest))]
[JsonSerializable(typeof(PlanDto))]
[JsonSerializable(typeof(List<PlanVariantDto>))]
[JsonSerializable(typeof(PlannerLabSettings))]
[JsonSerializable(typeof(PlannerLabImportRequest))]
[JsonSerializable(typeof(PlannerLabImportResponse))]
[JsonSerializable(typeof(AiReadinessResponse))]
[JsonSerializable(typeof(AiProviderConfiguration))]
[JsonSerializable(typeof(ReactiveLabApplyRequest))]
[JsonSerializable(typeof(ReactiveLabApplyResponse))]
[JsonSerializable(typeof(ReactiveMapping))]
[JsonSerializable(typeof(List<ReactiveMapping>))]
[JsonSerializable(typeof(ReactivePreset))]
[JsonSerializable(typeof(List<ReactivePreset>))]
[JsonSerializable(typeof(ReactiveLabLocalState))]
[JsonSerializable(typeof(ReactiveLabMetadata))]
[JsonSerializable(typeof(StudioJobListResponse))]
[JsonSerializable(typeof(StudioJobActionResponse))]
[JsonSerializable(typeof(StudioJob))]
[JsonSerializable(typeof(TimelineUpdateRequest))]
[JsonSerializable(typeof(TimelineAutosaveRequest))]
[JsonSerializable(typeof(TimelineRenderRequest))]
[JsonSerializable(typeof(TimelineRenderResponse))]
[JsonSerializable(typeof(MotionPhraseRequest))]
[JsonSerializable(typeof(ApplyMotionGrammarRequest))]
[JsonSerializable(typeof(ApplyMotionGrammarResponse))]
[JsonSerializable(typeof(RecoveryApplyRequest))]
[JsonSerializable(typeof(VariantReviewDecisionRequest))]
[JsonSerializable(typeof(VariantReviewDecisionResponse))]
[JsonSerializable(typeof(LiveCuePublishRequest))]
[JsonSerializable(typeof(LiveCuePublishResponse))]
[JsonSerializable(typeof(WorldAdapterExportRequest))]
[JsonSerializable(typeof(WorldAdapterExportResponse))]
[JsonSerializable(typeof(UnrealBundleExportRequest))]
[JsonSerializable(typeof(UnrealBundleExportResponse))]
[JsonSerializable(typeof(UnrealImportPlanRequest))]
[JsonSerializable(typeof(UnrealImportPlanResponse))]
[JsonSerializable(typeof(UnrealReturnImportRequest))]
[JsonSerializable(typeof(UnrealReturnImportResponse))]
[JsonSerializable(typeof(SetupStatusResponse))]
[JsonSerializable(typeof(SetupTaskListResponse))]
[JsonSerializable(typeof(SetupTaskActionResponse))]
[JsonSerializable(typeof(SetupOperationResponse))]
[JsonSerializable(typeof(SetupOllamaPullRequest))]
[JsonSerializable(typeof(SetupProfileRequest))]
[JsonSerializable(typeof(SetupFullInstallRequest))]
[JsonSerializable(typeof(SetupComfyUiInstallRequest))]
[JsonSerializable(typeof(SetupComfyUiStartRequest))]
[JsonSerializable(typeof(SetupEdmgInstallRequest))]
[JsonSerializable(typeof(ModelCatalogueResponse))]
[JsonSerializable(typeof(ModelCatalogueEntry))]
[JsonSerializable(typeof(ModelPackEntry))]
[JsonSerializable(typeof(ModelTask))]
[JsonSerializable(typeof(ModelTaskListResponse))]
[JsonSerializable(typeof(ModelTaskActionResponse))]
[JsonSerializable(typeof(ModelBenchmarkRequest))]
[JsonSerializable(typeof(ModelBenchmarkResponse))]
[JsonSerializable(typeof(CivitaiImportRequest))]
[JsonSerializable(typeof(LocalModelImportRequest))]
[JsonSerializable(typeof(ModelImportResponse))]
[JsonSerializable(typeof(TensorRtCancelImportRequest))]
[JsonSerializable(typeof(TensorRtMigrationStatus))]
[JsonSerializable(typeof(TensorRtLegacyStatus))]
[JsonSerializable(typeof(TensorRtLegacyFile))]
[JsonSerializable(typeof(TensorRtCanonicalStatus))]
[JsonSerializable(typeof(TensorRtMigrationAvailability))]
[JsonSerializable(typeof(TensorRtDiskStatus))]
[JsonSerializable(typeof(JsonElement))]
[JsonSerializable(typeof(Dictionary<string, JsonElement>))]
[JsonSerializable(typeof(WorkspaceAssetsResponse))]
[JsonSerializable(typeof(ProjectHealthResponse))]
[JsonSerializable(typeof(ProjectRelinkResponse))]
[JsonSerializable(typeof(ProjectCollectResponse))]
[JsonSerializable(typeof(MusicGraphResponse))]
[JsonSerializable(typeof(LiveCuesResponse))]
[JsonSerializable(typeof(LiveAssetsResponse))]
[JsonSerializable(typeof(ApplyPlanToTimelineRequest))]
[JsonSerializable(typeof(ApplyPlanToTimelineResponse))]
[JsonSerializable(typeof(UpdatePlanVariantRequest))]
[JsonSerializable(typeof(UpdatePlanVariantResponse))]
[JsonSerializable(typeof(TemplatePackageDto))]
[JsonSerializable(typeof(ExportTemplatePackageResponse))]
[JsonSerializable(typeof(ImportTemplatePackageRequest))]
[JsonSerializable(typeof(ImportTemplatePackageResponse))]
[JsonSerializable(typeof(LoraSelection))]
[JsonSerializable(typeof(ControlNetUnit))]
[JsonSerializable(typeof(HiresFixSettings))]
[JsonSerializable(typeof(RefinerSettings))]
[JsonSerializable(typeof(OutpaintSettings))]
[JsonSerializable(typeof(RenderScenesRequest))]
[JsonSerializable(typeof(RenderMotionRequest))]
[JsonSerializable(typeof(TensorRtStandaloneRenderRequest))]
[JsonSerializable(typeof(AutoAnimateRequest))]
[JsonSerializable(typeof(ParseqMotionApplyRequest))]
[JsonSerializable(typeof(LayerMaskSpec))]
[JsonSerializable(typeof(LayeredAnimateRequest))]
[JsonSerializable(typeof(AssembleVideoRequest))]
[JsonSerializable(typeof(ExportDeforumRequest))]
[JsonSerializable(typeof(RenderIntentSection))]
[JsonSerializable(typeof(RenderConductorPlanRequest))]
[JsonSerializable(typeof(RenderConductorPromoteRequest))]
[JsonSerializable(typeof(PerformerWorkflowPlanRequest))]
[JsonSerializable(typeof(PerformerWorkflowRunRequest))]
public sealed partial class StudioJsonContext : JsonSerializerContext;
