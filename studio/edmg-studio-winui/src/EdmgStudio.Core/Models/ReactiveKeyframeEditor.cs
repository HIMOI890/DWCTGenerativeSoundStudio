using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace EdmgStudio.Core.Models;

/// <summary>A presentation projection; scheduling and time conversion remain in the backend.</summary>
public sealed class ReactiveKeyframeEditor
{
    private readonly JsonObject _source;

    public ReactiveKeyframeEditor(JsonElement source)
    {
        _source = JsonNode.Parse(source.GetRawText()) as JsonObject
            ?? throw new JsonException("A reactive keyframe must be an object.");
    }

    public string Id => _source["id"]?.ToString() ?? _source["source_id"]?.ToString() ?? "Keyframe";
    public string Timing => $"{ReadNumber("t", ReadNumber("time", 0)).ToString("0.###", CultureInfo.InvariantCulture)} s";
    public string Placement => $"{Timing} · frame {_source["frame"]?.ToString() ?? "—"} · sample {_source["sample"]?.ToString() ?? "—"}";
    public string Summary => $"{Timing} · strength {Strength:0.###} · zoom {Zoom:0.###}";
    public bool IsEditable => !string.Equals(_source["locked"]?.ToString(), "true", StringComparison.OrdinalIgnoreCase);
    public double Strength => ReadNumber("strength", ReadNumber("motion_score", 0.5));
    public double Zoom => ReadNumber("zoom", 1);

    public bool Refine(double strength, double zoom)
    {
        if (!IsEditable) throw new InvalidOperationException("This keyframe is locked.");
        if (!double.IsFinite(strength) || strength is < 0 or > 1)
            throw new ArgumentOutOfRangeException(nameof(strength), "Strength must be between 0 and 1.");
        if (!double.IsFinite(zoom) || zoom <= 0 || zoom > 20)
            throw new ArgumentOutOfRangeException(nameof(zoom), "Zoom must be greater than 0 and at most 20.");
        if (Strength == strength && Zoom == zoom) return false;
        _source["strength"] = strength;
        _source["zoom"] = zoom;
        return true;
    }

    public JsonElement ToJson()
    {
        using var document = JsonDocument.Parse(_source.ToJsonString());
        return document.RootElement.Clone();
    }

    private double ReadNumber(string name, double fallback) =>
        double.TryParse(_source[name]?.ToString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
        && double.IsFinite(value) ? value : fallback;
}
