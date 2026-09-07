using System.Text.Json;
using System.Text.Json.Serialization;

namespace EdmgStudio.Core.Models;

public sealed class PlannerLabSettings
{
    [JsonPropertyName("analysisFocus")]
    public string AnalysisFocus { get; init; } = "balanced";

    [JsonPropertyName("promptStyle")]
    public string PromptStyle { get; init; } = "cinematic";

    [JsonPropertyName("promptDetail")]
    public string PromptDetail { get; init; } = "standard";

    [JsonPropertyName("aspectRatio")]
    public string AspectRatio { get; init; } = "16:9";

    [JsonPropertyName("target")]
    public string Target { get; init; } = "general-video";

    [JsonPropertyName("sceneCount")]
    public int SceneCount { get; init; } = 8;

    [JsonPropertyName("subjectFocus")]
    public string SubjectFocus { get; init; } = string.Empty;

    [JsonPropertyName("creativeBrief")]
    public string CreativeBrief { get; init; } = string.Empty;

    [JsonPropertyName("negativePromptSeed")]
    public string NegativePromptSeed { get; init; } = string.Empty;

    [JsonPropertyName("selectedVariantMode")]
    public string SelectedVariantMode { get; init; } = "safe";

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed class PlannerLabImportRequest
{
    [JsonPropertyName("analysis")]
    public JsonElement Analysis { get; init; }

    [JsonPropertyName("plan")]
    public JsonElement Plan { get; init; }

    [JsonPropertyName("settings")]
    public required PlannerLabSettings Settings { get; init; }

    [JsonPropertyName("apply_timeline")]
    public bool ApplyTimeline { get; init; } = true;

    [JsonPropertyName("overwrite_timeline")]
    public bool OverwriteTimeline { get; init; } = true;

    [JsonPropertyName("expected_revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? ExpectedRevision { get; set; }
}

public sealed class PlannerLabImportResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("plan")]
    public required PlanDto Plan { get; init; }

    [JsonPropertyName("timeline")]
    public JsonElement Timeline { get; init; }

    [JsonPropertyName("visual_dna")]
    public JsonElement VisualDna { get; init; }

    [JsonPropertyName("visual_dna_hints")]
    public JsonElement VisualDnaHints { get; init; }
}

public sealed class AiReadinessResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("ai")]
    public JsonElement Ai { get; init; }

    [JsonPropertyName("ai_config")]
    public required AiProviderConfiguration AiConfiguration { get; init; }
}

public sealed class AiProviderConfiguration
{
    [JsonPropertyName("mode")]
    public string Mode { get; init; } = string.Empty;

    [JsonPropertyName("provider")]
    public string Provider { get; init; } = string.Empty;

    [JsonPropertyName("label")]
    public string Label { get; init; } = string.Empty;

    [JsonPropertyName("model")]
    public string? Model { get; init; }

    [JsonPropertyName("model_family")]
    public string? ModelFamily { get; init; }

    [JsonPropertyName("base_url")]
    public string? BaseUrl { get; init; }

    [JsonPropertyName("ready")]
    public bool? IsReady { get; init; }

    [JsonPropertyName("warning")]
    public string? Warning { get; init; }

    [JsonPropertyName("hint")]
    public string? Hint { get; init; }

    [JsonPropertyName("model_presets")]
    public List<JsonElement> ModelPresets { get; init; } = [];

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? AdditionalData { get; set; }
}

public sealed record PlannerCreativeSettings(
    string CreativeDirection,
    string VisualDna,
    string Constraints,
    string PromptSeed,
    string DirectorPreset,
    string MotionPreset,
    string AnimationPreset,
    string RenderPreset,
    string ConductorIntent);

public static class PlannerWorkflow
{
    private static readonly HashSet<string> SupportedModes =
        new(StringComparer.OrdinalIgnoreCase) { "auto", "ai", "local", "edmg_core" };

    public static string NormalizeMode(string? mode)
    {
        var normalized = (mode ?? string.Empty).Trim().ToLowerInvariant();
        return SupportedModes.Contains(normalized) ? normalized : "auto";
    }

    public static IReadOnlyList<string> Validate(PlanRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        var errors = new List<string>();
        if (request.NumberOfVariants is < 1 or > 10)
        {
            errors.Add("Variant count must be between 1 and 10.");
        }

        if (request.MaximumScenes is < 1 or > 64)
        {
            errors.Add("Maximum scenes must be between 1 and 64.");
        }

        if (string.IsNullOrWhiteSpace(request.UserNotes) &&
            string.IsNullOrWhiteSpace(request.StylePreferences))
        {
            errors.Add("Add a creative brief, prompt, or style direction before generating.");
        }

        return errors;
    }

    public static string BuildStylePreferences(PlannerCreativeSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        var values = new (string Label, string Value)[]
        {
            ("Creative direction", settings.CreativeDirection),
            ("Visual DNA", settings.VisualDna),
            ("Constraints", settings.Constraints),
            ("Prompt seed", settings.PromptSeed),
            ("Director preset", settings.DirectorPreset),
            ("Motion preset", settings.MotionPreset),
            ("Animation preset", settings.AnimationPreset),
            ("Render preset", settings.RenderPreset),
            ("Conductor intent", settings.ConductorIntent),
        };

        return string.Join(
            "; ",
            values
                .Where(item => !string.IsNullOrWhiteSpace(item.Value))
                .Select(item => $"{item.Label}: {item.Value.Trim()}"));
    }
}
