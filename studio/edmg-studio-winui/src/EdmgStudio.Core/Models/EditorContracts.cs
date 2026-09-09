using System.Text.Json;
using System.Text.Json.Serialization;

namespace EdmgStudio.Core.Models;

public sealed record DirectorUpdateRequest(
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision,
    [property: JsonPropertyName("document")] JsonElement Document);

public sealed record DirectorGenerationRequest(
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision,
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("instruction")] string Instruction,
    [property: JsonPropertyName("mode")] string Mode = "automatic",
    [property: JsonPropertyName("renderer_engine")] string RendererEngine = "automatic",
    [property: JsonPropertyName("allow_external")] bool AllowExternal = false);

public sealed record DirectorApplyRequest(
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision);

public sealed record DirectorWorkflowReviewRequest(
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision,
    [property: JsonPropertyName("draft_id")] string DraftId,
    [property: JsonPropertyName("document")] JsonElement? Document = null);

public sealed record DirectorReactiveReviewRequest(
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision,
    [property: JsonPropertyName("draft_id")] string DraftId,
    [property: JsonPropertyName("payload")] JsonElement Payload);

public sealed record EditorCommandRequest(
    [property: JsonPropertyName("operation_id")] string OperationId,
    [property: JsonPropertyName("expected_revision")] long ExpectedRevision,
    [property: JsonPropertyName("action")] string Action,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("timeline")] JsonElement? Timeline = null,
    [property: JsonPropertyName("operations")]
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    IReadOnlyList<JsonElement>? Operations = null);

public sealed class EditorHistoryState
{
    [JsonPropertyName("can_undo")] public bool CanUndo { get; init; }
    [JsonPropertyName("can_redo")] public bool CanRedo { get; init; }
    [JsonPropertyName("undo_label")] public string? UndoLabel { get; init; }
    [JsonPropertyName("redo_label")] public string? RedoLabel { get; init; }
    [JsonPropertyName("external_change")] public bool ExternalChange { get; init; }
}

public sealed class EditorState
{
    [JsonPropertyName("ok")] public bool Ok { get; init; }
    [JsonPropertyName("revision")] public long Revision { get; init; }
    [JsonPropertyName("timeline")] public JsonElement Timeline { get; init; }
    [JsonPropertyName("history")] public EditorHistoryState History { get; init; } = new();
    [JsonPropertyName("replayed")] public bool Replayed { get; init; }
}
