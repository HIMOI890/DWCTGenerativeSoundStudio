using System.Text.Json;
using System.Text.Json.Serialization;

namespace EdmgStudio.Core.Models;

public sealed class WorkspaceAssetPathDto
{
    [JsonPropertyName("path")]
    public string Path { get; init; } = string.Empty;
}

public sealed class WorkspaceAssetGroupsDto
{
    [JsonPropertyName("audio")]
    public List<WorkspaceAssetPathDto> Audio { get; init; } = [];

    [JsonPropertyName("refs")]
    public List<WorkspaceAssetPathDto> References { get; init; } = [];
}

public sealed class WorkspaceAssetsResponse
{
    [JsonPropertyName("project_id")]
    public string ProjectId { get; init; } = string.Empty;

    [JsonPropertyName("assets")]
    public WorkspaceAssetGroupsDto Assets { get; init; } = new();
}

public sealed class ProjectHealthIssueDto
{
    [JsonPropertyName("code")]
    public string Code { get; init; } = string.Empty;

    [JsonPropertyName("severity")]
    public string Severity { get; init; } = string.Empty;

    [JsonPropertyName("message")]
    public string Message { get; init; } = string.Empty;
}

public sealed class ProjectHealthAssetDto
{
    [JsonPropertyName("path")]
    public string Path { get; init; } = string.Empty;

    [JsonPropertyName("role")]
    public string Role { get; init; } = string.Empty;

    [JsonPropertyName("exists")]
    public bool Exists { get; init; }

    [JsonPropertyName("bytes")]
    public long? Bytes { get; init; }

    [JsonPropertyName("referenced")]
    public bool Referenced { get; init; }
}

public sealed class ProjectMissingAssetDto
{
    [JsonPropertyName("path")]
    public string Path { get; init; } = string.Empty;

    [JsonPropertyName("reason")]
    public string Reason { get; init; } = string.Empty;
}

public sealed class ProjectAssetIndexDto
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("generated_at")]
    public string GeneratedAt { get; init; } = string.Empty;

    [JsonPropertyName("asset_count")]
    public int AssetCount { get; init; }

    [JsonPropertyName("missing_count")]
    public int MissingCount { get; init; }

    [JsonPropertyName("total_bytes")]
    public long TotalBytes { get; init; }

    [JsonPropertyName("disk_estimate_gb")]
    public double DiskEstimateGb { get; init; }

    [JsonPropertyName("missing")]
    public List<ProjectMissingAssetDto> Missing { get; init; } = [];

    [JsonPropertyName("assets")]
    public List<ProjectHealthAssetDto> Assets { get; init; } = [];
}

public sealed class ProjectHealthDto
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("status")]
    public string Status { get; init; } = string.Empty;

    [JsonPropertyName("issues")]
    public List<ProjectHealthIssueDto> Issues { get; init; } = [];

    [JsonPropertyName("asset_index")]
    public ProjectAssetIndexDto AssetIndex { get; init; } = new();

    [JsonPropertyName("actions")]
    public List<string> Actions { get; init; } = [];
}

public sealed class ProjectHealthResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("health")]
    public ProjectHealthDto Health { get; init; } = new();
}

public sealed class ProjectRelinkSuggestionDto
{
    [JsonPropertyName("missing")]
    public string Missing { get; init; } = string.Empty;

    [JsonPropertyName("candidate")]
    public string Candidate { get; init; } = string.Empty;
}

public sealed class ProjectRelinkResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("suggestions")]
    public List<ProjectRelinkSuggestionDto> Suggestions { get; init; } = [];

    [JsonPropertyName("missing_count")]
    public int MissingCount { get; init; }
}

public sealed class ProjectCollectResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("dest")]
    public string Destination { get; init; } = string.Empty;

    [JsonPropertyName("copied_count")]
    public int CopiedCount { get; init; }

    [JsonPropertyName("skipped_count")]
    public int SkippedCount { get; init; }

    [JsonPropertyName("copied")]
    public List<string> Copied { get; init; } = [];

    [JsonPropertyName("skipped")]
    public List<string> Skipped { get; init; } = [];
}

public sealed class MusicGraphTimebaseDto
{
    [JsonPropertyName("sampleRate")]
    public int SampleRate { get; init; }

    [JsonPropertyName("durationSeconds")]
    public double DurationSeconds { get; init; }
}

public sealed class MusicGraphTempoDto
{
    [JsonPropertyName("bpm")]
    public double Bpm { get; init; }

    [JsonPropertyName("confidence")]
    public double Confidence { get; init; }
}

public sealed class MusicGraphSectionDto
{
    [JsonPropertyName("start")]
    public double Start { get; init; }

    [JsonPropertyName("end")]
    public double End { get; init; }

    [JsonPropertyName("label")]
    public string Label { get; init; } = string.Empty;

    [JsonPropertyName("energy")]
    public double? Energy { get; init; }
}

public sealed class MusicGraphSemanticsDto
{
    [JsonPropertyName("tags")]
    public List<string> Tags { get; init; } = [];
}

public sealed class MusicGraphResponse
{
    private MusicGraphTimebaseDto _timebase = new();
    private MusicGraphTempoDto _tempo = new();
    private List<JsonElement> _beats = [];
    private List<MusicGraphSectionDto> _sections = [];
    private List<JsonElement> _stems = [];
    private List<string> _confidenceNotes = [];

    [JsonPropertyName("schemaVersion")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("timebase")]
    public MusicGraphTimebaseDto Timebase
    {
        get => _timebase;
        init => _timebase = value ?? new();
    }

    [JsonPropertyName("tempo")]
    public MusicGraphTempoDto Tempo
    {
        get => _tempo;
        init => _tempo = value ?? new();
    }

    [JsonPropertyName("beats")]
    public List<JsonElement> Beats
    {
        get => _beats;
        init => _beats = value ?? [];
    }

    [JsonPropertyName("sections")]
    public List<MusicGraphSectionDto> Sections
    {
        get => _sections;
        init => _sections = value ?? [];
    }

    [JsonPropertyName("stems")]
    public List<JsonElement> Stems
    {
        get => _stems;
        init => _stems = value ?? [];
    }

    [JsonPropertyName("confidenceNotes")]
    public List<string> ConfidenceNotes
    {
        get => _confidenceNotes;
        init => _confidenceNotes = value ?? [];
    }

    [JsonPropertyName("semantics")]
    public MusicGraphSemanticsDto? Semantics { get; init; }
}

public sealed class LiveCuesResponse
{
    private List<JsonElement> _events = [];
    private List<string> _notes = [];

    [JsonPropertyName("schemaVersion")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("advisory_only")]
    public bool AdvisoryOnly { get; init; }

    [JsonPropertyName("bpm")]
    public double Bpm { get; init; }

    [JsonPropertyName("duration_s")]
    public double DurationSeconds { get; init; }

    [JsonPropertyName("event_count")]
    public int EventCount { get; init; }

    [JsonPropertyName("events")]
    public List<JsonElement> Events
    {
        get => _events;
        init => _events = value ?? [];
    }

    [JsonPropertyName("notes")]
    public List<string> Notes
    {
        get => _notes;
        init => _notes = value ?? [];
    }
}

public sealed class LiveAssetsResponse
{
    private List<JsonElement> _packs = [];

    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("ready")]
    public bool Ready { get; init; }

    [JsonPropertyName("never_blocks_on_diffusion")]
    public bool NeverBlocksOnDiffusion { get; init; }

    [JsonPropertyName("latency_budget_ms")]
    public int LatencyBudgetMilliseconds { get; init; }

    [JsonPropertyName("max_update_hz")]
    public int MaxUpdateHz { get; init; }

    [JsonPropertyName("duration_s")]
    public double DurationSeconds { get; init; }

    [JsonPropertyName("pack_count")]
    public int PackCount { get; init; }

    [JsonPropertyName("channel_count")]
    public int ChannelCount { get; init; }

    [JsonPropertyName("packs")]
    public List<JsonElement> Packs
    {
        get => _packs;
        init => _packs = value ?? [];
    }
}

public sealed class ApplyPlanToTimelineRequest
{
    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("overwrite")]
    public bool Overwrite { get; init; }

    [JsonPropertyName("expected_revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? ExpectedRevision { get; init; }
}

public sealed class ApplyPlanToTimelineResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("timeline")]
    public JsonElement Timeline { get; init; }

    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }
}

public sealed class UpdatePlanVariantRequest
{
    [JsonPropertyName("variant_index")]
    public int VariantIndex { get; init; }

    [JsonPropertyName("scenes")]
    public IReadOnlyList<PlanSceneDto> Scenes { get; init; } = [];

    [JsonPropertyName("expected_revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? ExpectedRevision { get; init; }
}

public sealed class UpdatePlanVariantResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("plan")]
    public PlanDto? Plan { get; init; }

    [JsonPropertyName("variant_index")]
    public int? VariantIndex { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class TemplatePackageDto
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("payload")]
    public JsonElement Payload { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class ExportTemplatePackageResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("package")]
    public TemplatePackageDto Package { get; init; } = new();
}

public sealed class ImportTemplatePackageRequest
{
    [JsonPropertyName("package")]
    public TemplatePackageDto Package { get; init; } = new();

    [JsonPropertyName("merge")]
    public bool Merge { get; init; } = true;

    [JsonPropertyName("expected_revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? ExpectedRevision { get; init; }
}

public sealed class ImportTemplatePackageResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("applied")]
    public List<string> Applied { get; init; } = [];

    [JsonPropertyName("project")]
    public ProjectDto? Project { get; init; }
}

public static class WorkspaceModelHelpers
{
    private const string ApprovedKey = "approved";
    private const string LockedKey = "locked";
    private const string StatusKey = "status";

    private static readonly HashSet<string> ImportableTemplateFields =
    [
        "visual_dna",
        "director_mode",
        "animation_preset",
        "render_preset",
        "conductor_intent",
    ];

    public static int ClampVariantIndex(int selectedIndex, int variantCount) =>
        variantCount <= 0 ? 0 : Math.Clamp(selectedIndex, 0, variantCount - 1);

    public static IReadOnlyList<PlanSceneDto> MoveScene(
        IReadOnlyList<PlanSceneDto> scenes,
        int sceneIndex,
        int offset)
    {
        ArgumentNullException.ThrowIfNull(scenes);
        if (sceneIndex < 0 || sceneIndex >= scenes.Count)
        {
            throw new ArgumentOutOfRangeException(nameof(sceneIndex));
        }

        int targetIndex = Math.Clamp(sceneIndex + offset, 0, scenes.Count - 1);
        var timingSlots = scenes
            .Select(scene => (scene.StartSeconds, scene.EndSeconds))
            .ToArray();
        var reordered = scenes.ToList();
        if (targetIndex != sceneIndex)
        {
            PlanSceneDto scene = reordered[sceneIndex];
            reordered.RemoveAt(sceneIndex);
            reordered.Insert(targetIndex, scene);
        }

        return reordered
            .Select((scene, index) => CloneScene(
                scene,
                startSeconds: timingSlots[index].StartSeconds,
                endSeconds: timingSlots[index].EndSeconds))
            .ToArray();
    }

    public static PlanSceneDto CloneScene(
        PlanSceneDto scene,
        double? startSeconds = null,
        double? endSeconds = null,
        string? prompt = null,
        string? negativePrompt = null,
        bool replaceNegativePrompt = false,
        string? setting = null,
        string? shotType = null,
        string? characterLock = null,
        string? styleLock = null,
        string? startState = null,
        string? endState = null,
        string? subject = null,
        string? action = null,
        string? camera = null,
        string? motion = null,
        string? environmentMotion = null,
        string? continuity = null,
        string? transition = null,
        bool replaceStoryboardFields = false)
    {
        ArgumentNullException.ThrowIfNull(scene);
        return new PlanSceneDto
        {
            StartSeconds = startSeconds ?? scene.StartSeconds,
            EndSeconds = endSeconds ?? scene.EndSeconds,
            Prompt = prompt ?? scene.Prompt,
            NegativePrompt = replaceNegativePrompt ? negativePrompt : scene.NegativePrompt,
            Setting = replaceStoryboardFields ? setting : scene.Setting,
            ShotType = replaceStoryboardFields ? shotType : scene.ShotType,
            CharacterLock = replaceStoryboardFields ? characterLock : scene.CharacterLock,
            StyleLock = replaceStoryboardFields ? styleLock : scene.StyleLock,
            StartState = replaceStoryboardFields ? startState : scene.StartState,
            EndState = replaceStoryboardFields ? endState : scene.EndState,
            Subject = replaceStoryboardFields ? subject : scene.Subject,
            Action = replaceStoryboardFields ? action : scene.Action,
            Camera = replaceStoryboardFields ? camera : scene.Camera,
            Motion = replaceStoryboardFields ? motion : scene.Motion,
            EnvironmentMotion = replaceStoryboardFields ? environmentMotion : scene.EnvironmentMotion,
            ContinuityNote = replaceStoryboardFields ? continuity : scene.ContinuityNote,
            Transition = replaceStoryboardFields ? transition : scene.Transition,
            AdditionalData = CloneAdditionalData(scene.AdditionalData),
        };
    }

    public static IReadOnlyList<PlanSceneDto> NormalizeStoryboardContinuity(
        IReadOnlyList<PlanSceneDto> scenes,
        string? characterLockOverride = null,
        string? styleLockOverride = null)
    {
        ArgumentNullException.ThrowIfNull(scenes);
        string? characterLock = !string.IsNullOrWhiteSpace(characterLockOverride)
            ? characterLockOverride
            : scenes
                .Select(scene => scene.CharacterLock)
                .FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));
        string? styleLock = !string.IsNullOrWhiteSpace(styleLockOverride)
            ? styleLockOverride
            : scenes
                .Select(scene => scene.StyleLock)
                .FirstOrDefault(value => !string.IsNullOrWhiteSpace(value));
        string? previousEndState = null;
        var normalized = new List<PlanSceneDto>(scenes.Count);

        foreach (PlanSceneDto scene in scenes)
        {
            string? startStateSource = string.IsNullOrWhiteSpace(previousEndState)
                ? scene.StartState
                : previousEndState;
            string? startState = ReplaceStoryboardContractValue(
                startStateSource,
                scene.CharacterLock,
                characterLock);
            string? endState = ReplaceStoryboardContractValue(
                scene.EndState,
                scene.CharacterLock,
                characterLock);
            string prompt = scene.Prompt;
            prompt = ReplaceStoryboardContractValue(prompt, scene.CharacterLock, characterLock) ?? string.Empty;
            prompt = ReplaceStoryboardContractValue(prompt, scene.StyleLock, styleLock) ?? string.Empty;
            prompt = ReplaceStoryboardContractValue(prompt, scene.StartState, startState) ?? string.Empty;
            prompt = ReplaceStoryboardContractValue(prompt, scene.EndState, endState) ?? string.Empty;
            PlanSceneDto clone = CloneScene(
                scene,
                prompt: prompt,
                setting: scene.Setting,
                shotType: scene.ShotType,
                characterLock: characterLock,
                styleLock: styleLock,
                startState: startState,
                endState: endState,
                subject: ReplaceStoryboardContractValue(
                    scene.Subject,
                    scene.CharacterLock,
                    characterLock),
                action: scene.Action,
                camera: scene.Camera,
                motion: ReplaceStoryboardContractValue(
                    scene.Motion,
                    scene.CharacterLock,
                    characterLock),
                environmentMotion: scene.EnvironmentMotion,
                continuity: ReplaceStoryboardContractValue(
                    scene.ContinuityInstruction,
                    scene.CharacterLock,
                    characterLock),
                transition: scene.Transition,
                replaceStoryboardFields: true);
            normalized.Add(clone);
            previousEndState = clone.EndState;
        }

        return normalized;
    }

    private static string? ReplaceStoryboardContractValue(
        string? source,
        string? previous,
        string? next)
    {
        if (string.IsNullOrEmpty(source) ||
            string.IsNullOrWhiteSpace(previous) ||
            string.IsNullOrWhiteSpace(next) ||
            string.Equals(previous, next, StringComparison.Ordinal))
        {
            return source;
        }

        return source.Replace(previous, next, StringComparison.Ordinal);
    }

    public static bool IsSceneApproved(PlanSceneDto scene) =>
        ReadBoolean(scene, ApprovedKey);

    public static bool IsSceneLocked(PlanSceneDto scene) =>
        ReadBoolean(scene, LockedKey);

    public static string GetSceneStatus(PlanSceneDto scene)
    {
        if (scene.AdditionalData?.TryGetValue(StatusKey, out JsonElement value) == true &&
            value.ValueKind == JsonValueKind.String)
        {
            return value.GetString() ?? "draft";
        }

        return IsSceneApproved(scene) ? "approved" : "draft";
    }

    public static PlanSceneDto SetSceneApproval(PlanSceneDto scene, bool approved)
    {
        PlanSceneDto clone = CloneScene(scene);
        clone.AdditionalData ??= [];
        clone.AdditionalData[ApprovedKey] = CreateBooleanElement(approved);
        if (approved)
        {
            clone.AdditionalData[StatusKey] = CreateStringElement("approved");
        }
        else if (string.Equals(GetSceneStatus(scene), "approved", StringComparison.OrdinalIgnoreCase))
        {
            clone.AdditionalData[StatusKey] = CreateStringElement("draft");
        }

        return clone;
    }

    public static PlanSceneDto SetSceneLocked(PlanSceneDto scene, bool locked)
    {
        PlanSceneDto clone = CloneScene(scene);
        clone.AdditionalData ??= [];
        clone.AdditionalData[LockedKey] = CreateBooleanElement(locked);
        return clone;
    }

    public static PlanSceneDto MarkSceneNeedsRepair(PlanSceneDto scene)
    {
        PlanSceneDto clone = SetSceneApproval(scene, approved: false);
        clone.AdditionalData ??= [];
        clone.AdditionalData[StatusKey] = CreateStringElement("needs-repair");
        return clone;
    }

    private static bool ReadBoolean(PlanSceneDto scene, string key) =>
        scene.AdditionalData?.TryGetValue(key, out JsonElement value) == true &&
        value.ValueKind is JsonValueKind.True or JsonValueKind.False &&
        value.GetBoolean();

    private static Dictionary<string, JsonElement>? CloneAdditionalData(
        IReadOnlyDictionary<string, JsonElement>? additionalData) =>
        additionalData?.ToDictionary(
            pair => pair.Key,
            pair => pair.Value.Clone(),
            StringComparer.Ordinal);

    private static JsonElement CreateBooleanElement(bool value)
    {
        using JsonDocument document = JsonDocument.Parse(value ? "true" : "false");
        return document.RootElement.Clone();
    }

    private static JsonElement CreateStringElement(string value)
    {
        string encoded = JsonEncodedText.Encode(value).ToString();
        using JsonDocument document = JsonDocument.Parse($"\"{encoded}\"");
        return document.RootElement.Clone();
    }

    public static TemplatePackageDto ParseTemplatePackage(string json)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            throw new ArgumentException("Template package JSON is required.", nameof(json));
        }

        TemplatePackageDto package = JsonSerializer.Deserialize(
                json,
                StudioJson.GetTypeInfo<TemplatePackageDto>())
            ?? throw new JsonException("Template package JSON is empty.");

        if (package.SchemaVersion != 1)
        {
            throw new JsonException("Template package schema_version must be 1.");
        }

        if (package.Payload.ValueKind != JsonValueKind.Object)
        {
            throw new JsonException("Template package payload must be an object.");
        }

        bool hasImportableField = package.Payload.EnumerateObject()
            .Any(property => ImportableTemplateFields.Contains(property.Name));
        if (!hasImportableField)
        {
            throw new JsonException("Template package payload has no importable Workspace fields.");
        }

        return package;
    }
}
