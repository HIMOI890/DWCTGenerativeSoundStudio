using System.Globalization;
using System.Text.Json.Nodes;

namespace EdmgStudio.WinUI.Pages;

/// <summary>A presentation projection over the complete shared scene JSON.</summary>
public sealed class WorkspaceDirectionSceneItem(JsonObject scene, int sampleRate, bool draftEditable, Action changed)
{
    public string Timing => $"{Text("scene_id")} · {Seconds("start_sample"):0.00}s – {Seconds("end_sample"):0.00}s{(IsLocked ? " · locked" : string.Empty)}";

    private bool IsLocked => scene["renderer_hints"]?["locked"]?.GetValue<bool>() == true;

    public bool IsEditable => draftEditable && !IsLocked;

    public string Intent
    {
        get => Text("intent");
        set { scene["intent"] = value; changed(); }
    }

    public string Camera
    {
        get => scene["camera"]?["movement"]?.GetValue<string>() ?? string.Empty;
        set
        {
            JsonObject camera = scene["camera"] as JsonObject ?? new JsonObject();
            camera["movement"] = value;
            scene["camera"] = camera;
            changed();
        }
    }

    public string Actions
    {
        get => string.Join(Environment.NewLine, (scene["actions"] as JsonArray ?? []).Select(item => item?.GetValue<string>() ?? string.Empty));
        set
        {
            scene["actions"] = new JsonArray(value.Split(['\r', '\n'], StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Select(item => (JsonNode?)JsonValue.Create(item)).ToArray());
            changed();
        }
    }

    private string Text(string key) => scene[key]?.GetValue<string>() ?? string.Empty;

    private double Seconds(string key) => long.TryParse(Text(key), NumberStyles.Integer, CultureInfo.InvariantCulture, out long sample)
        ? sample / (double)sampleRate : 0;
}
