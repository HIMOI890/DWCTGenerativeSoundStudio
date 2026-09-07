using System.Text.Json;
using System.Text.Json.Serialization;

namespace EdmgStudio.Core.Models;

public sealed class ReactiveLabApplyRequest
{
    [JsonPropertyName("metadata")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? Metadata { get; init; }

    [JsonPropertyName("keyframes")]
    public List<JsonElement> Keyframes { get; init; } = [];

    [JsonPropertyName("beat_markers")]
    public List<JsonElement> BeatMarkers { get; init; } = [];

    [JsonPropertyName("cue_events")]
    public List<JsonElement> CueEvents { get; init; } = [];

    [JsonPropertyName("sections")]
    public List<JsonElement> Sections { get; init; } = [];

    [JsonPropertyName("repair_suggestions")]
    public List<JsonElement> RepairSuggestions { get; init; } = [];

    [JsonPropertyName("schedules")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? Schedules { get; init; }

    [JsonPropertyName("handoff_manifest")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? HandoffManifest { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }

    [JsonPropertyName("overwrite_motion_track")]
    public bool OverwriteMotionTrack { get; init; } = true;

    [JsonPropertyName("overwrite_camera")]
    public bool OverwriteCamera { get; init; } = true;

    [JsonPropertyName("expected_revision")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public long? ExpectedRevision { get; init; }
}

public sealed class ReactiveLabApplyResponse
{
    [JsonPropertyName("ok")]
    public bool Ok { get; init; }

    [JsonPropertyName("timeline")]
    public JsonElement Timeline { get; init; }

    [JsonPropertyName("visual_dna")]
    public JsonElement VisualDna { get; init; }

    [JsonPropertyName("visual_dna_hints")]
    public JsonElement VisualDnaHints { get; init; }
}

public sealed record ReactiveMapping
{
    [JsonPropertyName("id")]
    public string Id { get; set; } = Guid.NewGuid().ToString("N");

    [JsonPropertyName("name")]
    public string Name { get; set; } = "New mapping";

    [JsonPropertyName("enabled")]
    public bool IsEnabled { get; set; } = true;

    [JsonPropertyName("source_signal")]
    public string SourceSignal { get; set; } = "energy";

    [JsonPropertyName("target_parameter")]
    public string TargetParameter { get; set; } = "motion.strength";

    [JsonPropertyName("response_curve")]
    public string ResponseCurve { get; set; } = "linear";

    [JsonPropertyName("grammar")]
    public string Grammar { get; set; } = "continuous";

    [JsonPropertyName("gain")]
    public double Gain { get; set; } = 1;

    [JsonPropertyName("smoothing")]
    public double Smoothing { get; set; } = 0.25;

    [JsonPropertyName("threshold")]
    public double Threshold { get; set; } = 0.1;

    [JsonPropertyName("input_min")]
    public double InputMinimum { get; set; }

    [JsonPropertyName("input_max")]
    public double InputMaximum { get; set; } = 1;

    [JsonPropertyName("output_min")]
    public double OutputMinimum { get; set; }

    [JsonPropertyName("output_max")]
    public double OutputMaximum { get; set; } = 1;

    [JsonPropertyName("quantization")]
    public string Quantization { get; set; } = "none";

    [JsonPropertyName("section")]
    public string? Section { get; set; }

    [JsonPropertyName("cue")]
    public string? Cue { get; set; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }
}

public sealed class ReactivePreset
{
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    [JsonPropertyName("mappings")]
    public List<ReactiveMapping> Mappings { get; init; } = [];

    [JsonPropertyName("mapping_preset")]
    public string MappingPreset { get; init; } = "cinematic";

    [JsonPropertyName("sensitivity")]
    public double Sensitivity { get; init; } = 1;

    [JsonPropertyName("smoothing")]
    public double Smoothing { get; init; } = 0.82;

    [JsonPropertyName("fps")]
    public int FramesPerSecond { get; init; } = 30;

    [JsonPropertyName("min_cut_frames")]
    public int MinimumCutFrames { get; init; } = 12;

    [JsonPropertyName("render_mode")]
    public string RenderMode { get; init; } = "balanced";

    [JsonPropertyName("schedule_stride")]
    public int ScheduleStride { get; init; } = 4;

    [JsonPropertyName("scaling")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonElement? Scaling { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }
}

public sealed class ReactiveLabLocalState
{
    [JsonPropertyName("current")]
    public ReactivePreset Current { get; init; } = new();

    [JsonPropertyName("presets")]
    public List<ReactivePreset> Presets { get; init; } = [];
}

public sealed class ReactiveLabMetadata
{
    [JsonPropertyName("source")]
    public string Source { get; init; } = "winui";

    [JsonPropertyName("selected_variant_index")]
    public int? SelectedVariantIndex { get; init; }

    [JsonPropertyName("mappings")]
    public List<ReactiveMapping> Mappings { get; init; } = [];

    [JsonPropertyName("settings")]
    public ReactivePreset Settings { get; init; } = new();

    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }
}

public static class ReactiveWorkflow
{
    public static IReadOnlyList<string> ValidateMapping(ReactiveMapping mapping)
    {
        ArgumentNullException.ThrowIfNull(mapping);
        var errors = new List<string>();
        if (string.IsNullOrWhiteSpace(mapping.Name))
        {
            errors.Add("Mapping name is required.");
        }

        if (string.IsNullOrWhiteSpace(mapping.SourceSignal))
        {
            errors.Add("Source signal is required.");
        }

        if (string.IsNullOrWhiteSpace(mapping.TargetParameter))
        {
            errors.Add("Target parameter is required.");
        }

        if (!double.IsFinite(mapping.Gain) || mapping.Gain < 0)
        {
            errors.Add("Gain must be a finite value greater than or equal to zero.");
        }

        if (!double.IsFinite(mapping.Smoothing) || mapping.Smoothing is < 0 or > 1)
        {
            errors.Add("Smoothing must be between 0 and 1.");
        }

        if (!double.IsFinite(mapping.Threshold) || mapping.Threshold is < 0 or > 1)
        {
            errors.Add("Threshold must be between 0 and 1.");
        }

        if (mapping.InputMinimum >= mapping.InputMaximum)
        {
            errors.Add("Input minimum must be less than input maximum.");
        }

        if (mapping.OutputMinimum >= mapping.OutputMaximum)
        {
            errors.Add("Output minimum must be less than output maximum.");
        }

        return errors;
    }

    public static ReactiveMapping Duplicate(ReactiveMapping mapping, string newId)
    {
        ArgumentNullException.ThrowIfNull(mapping);
        if (string.IsNullOrWhiteSpace(newId))
        {
            throw new ArgumentException("A duplicate mapping ID is required.", nameof(newId));
        }

        return mapping with { Id = newId.Trim(), Name = $"{mapping.Name} copy" };
    }

    public static IReadOnlyList<ReactiveMapping> Move(
        IReadOnlyList<ReactiveMapping> mappings,
        int fromIndex,
        int toIndex)
    {
        ArgumentNullException.ThrowIfNull(mappings);
        if ((uint)fromIndex >= (uint)mappings.Count)
        {
            throw new ArgumentOutOfRangeException(nameof(fromIndex));
        }

        if ((uint)toIndex >= (uint)mappings.Count)
        {
            throw new ArgumentOutOfRangeException(nameof(toIndex));
        }

        var result = mappings.ToList();
        var item = result[fromIndex];
        result.RemoveAt(fromIndex);
        result.Insert(toIndex, item);
        return result;
    }

    public static bool HasMeaningfulPayload(ReactiveLabApplyRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        return request.Keyframes.Count > 0 ||
               request.BeatMarkers.Count > 0 ||
               request.CueEvents.Count > 0 ||
               request.Sections.Count > 0 ||
               request.RepairSuggestions.Count > 0 ||
               HasObjectContent(request.Schedules) ||
               HasObjectContent(request.HandoffManifest);
    }

    public static JsonElement MergeMappingsIntoMetadata(
        JsonElement metadata,
        IReadOnlyList<ReactiveMapping> mappings)
    {
        ArgumentNullException.ThrowIfNull(mappings);
        var values = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        if (metadata.ValueKind == JsonValueKind.Object)
        {
            foreach (var property in metadata.EnumerateObject())
            {
                values[property.Name] = property.Value.Clone();
            }
        }

        values["native_mappings"] = JsonSerializer.SerializeToElement(
            mappings.ToList(),
            StudioJson.GetTypeInfo<List<ReactiveMapping>>());
        return JsonSerializer.SerializeToElement(
            values,
            StudioJson.GetTypeInfo<Dictionary<string, JsonElement>>());
    }

    private static bool HasObjectContent(JsonElement? element) =>
        element?.ValueKind switch
        {
            JsonValueKind.Object => element.Value.EnumerateObject().Any(),
            JsonValueKind.Array => element.Value.GetArrayLength() > 0,
            _ => false,
        };
}
