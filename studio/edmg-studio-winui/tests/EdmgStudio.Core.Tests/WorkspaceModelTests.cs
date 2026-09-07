using System.Text.Json;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class WorkspaceModelTests
{
    [TestMethod]
    public void MoveScene_RoundTripsExactHandoffsWithoutMutatingSource()
    {
        PlanSceneDto[] scenes = Enumerable.Range(0, 3).Select(index => new PlanSceneDto
        {
            StartSeconds = index * 4, EndSeconds = (index + 1) * 4,
            Setting = $"station {index}", ShotType = "tracking",
            CharacterLock = "driver", StyleLock = "silver grain",
            StartState = $"start {index}", EndState = $"end {index}",
            Prompt = $"Start state: start {index}. End state: end {index}.",
        }).ToArray();
        var moved = WorkspaceModelHelpers.MoveScene(scenes, 2, -2);
        var request = new UpdatePlanVariantRequest { Scenes = moved };
        var json = JsonSerializer.Serialize(request, StudioJson.GetTypeInfo<UpdatePlanVariantRequest>());
        var reloaded = JsonSerializer.Deserialize(json, StudioJson.GetTypeInfo<UpdatePlanVariantRequest>())!.Scenes;
        Assert.AreEqual("station 2", reloaded[0].Setting);
        Assert.AreEqual("tracking", reloaded[0].ShotType);
        Assert.AreEqual("driver", reloaded[0].CharacterLock);
        Assert.AreEqual("silver grain", reloaded[0].StyleLock);
        for (int index = 1; index < reloaded.Count; index++)
        {
            Assert.AreEqual(reloaded[index - 1].EndState, reloaded[index].StartState);
            StringAssert.Contains(reloaded[index].Prompt, $"Start state: {reloaded[index - 1].EndState}.");
        }
        Assert.AreEqual("start 0", scenes[0].StartState);
    }

    [TestMethod]
    public void ClampVariantIndex_UsesAvailableVariantRange()
    {
        Assert.AreEqual(0, WorkspaceModelHelpers.ClampVariantIndex(-4, 3));
        Assert.AreEqual(1, WorkspaceModelHelpers.ClampVariantIndex(1, 3));
        Assert.AreEqual(2, WorkspaceModelHelpers.ClampVariantIndex(8, 3));
        Assert.AreEqual(0, WorkspaceModelHelpers.ClampVariantIndex(8, 0));
    }

    [TestMethod]
    public void MoveScene_ReordersCompleteSceneObjectsDeterministically()
    {
        using var metadata = JsonDocument.Parse("""{"camera":"orbit"}""");
        var scenes = new[]
        {
            new PlanSceneDto { StartSeconds = 0, EndSeconds = 4, Prompt = "First" },
            new PlanSceneDto
            {
                StartSeconds = 4,
                EndSeconds = 8,
                Prompt = "Second",
                AdditionalData = new Dictionary<string, JsonElement>
                {
                    ["metadata"] = metadata.RootElement.GetProperty("camera").Clone(),
                },
            },
            new PlanSceneDto { StartSeconds = 8, EndSeconds = 12, Prompt = "Third" },
        };

        var reordered = WorkspaceModelHelpers.MoveScene(scenes, 1, -1);

        CollectionAssert.AreEqual(
            new[] { "Second", "First", "Third" },
            reordered.Select(scene => scene.Prompt).ToArray());
        CollectionAssert.AreEqual(
            new[] { 0D, 4D, 8D },
            reordered.Select(scene => scene.StartSeconds).ToArray());
        CollectionAssert.AreEqual(
            new[] { 4D, 8D, 12D },
            reordered.Select(scene => scene.EndSeconds).ToArray());
        Assert.AreEqual("orbit", reordered[0].AdditionalData!["metadata"].GetString());
        Assert.AreEqual("First", scenes[0].Prompt);
    }

    [TestMethod]
    public void CloneScene_PreservesMetadataAfterSourceDocumentIsDisposed()
    {
        PlanSceneDto clone;
        using (var metadata = JsonDocument.Parse("""{"continuity":{"subject":"performer"},"score":0.92}"""))
        {
            var source = new PlanSceneDto
            {
                StartSeconds = 2,
                EndSeconds = 6,
                Prompt = "Tracking shot",
                NegativePrompt = "flicker",
                Setting = "flooded conservatory with a broken east window",
                ShotType = "medium-wide low-angle composition",
                CharacterLock = "copper automaton with one blue glass eye and a red scarf",
                StyleLock = "oxidized oil-paint texture with amber and teal lighting",
                StartState = "automaton faces right beside the east window, left hand lowered",
                EndState = "automaton still faces right, left hand touching the window latch",
                Subject = "same copper automaton",
                Action = "turns and reaches",
                Camera = "left-to-right track",
                Motion = "head and hand movement",
                EnvironmentMotion = "orchids and rain move",
                ContinuityNote = "preserve blue eye and screen direction",
                Transition = "match action",
                AdditionalData = new Dictionary<string, JsonElement>
                {
                    ["continuity"] = metadata.RootElement.GetProperty("continuity"),
                    ["score"] = metadata.RootElement.GetProperty("score"),
                },
            };

            clone = WorkspaceModelHelpers.CloneScene(source);
        }

        Assert.AreEqual(2D, clone.StartSeconds);
        Assert.AreEqual(6D, clone.EndSeconds);
        Assert.AreEqual("Tracking shot", clone.Prompt);
        Assert.AreEqual("flicker", clone.NegativePrompt);
        Assert.AreEqual("flooded conservatory with a broken east window", clone.Setting);
        Assert.AreEqual("medium-wide low-angle composition", clone.ShotType);
        Assert.AreEqual("copper automaton with one blue glass eye and a red scarf", clone.CharacterLock);
        Assert.AreEqual("oxidized oil-paint texture with amber and teal lighting", clone.StyleLock);
        Assert.AreEqual("automaton faces right beside the east window, left hand lowered", clone.StartState);
        Assert.AreEqual("automaton still faces right, left hand touching the window latch", clone.EndState);
        Assert.AreEqual("same copper automaton", clone.Subject);
        Assert.AreEqual("turns and reaches", clone.Action);
        Assert.AreEqual("left-to-right track", clone.Camera);
        Assert.AreEqual("head and hand movement", clone.Motion);
        Assert.AreEqual("orchids and rain move", clone.EnvironmentMotion);
        Assert.AreEqual("preserve blue eye and screen direction", clone.ContinuityNote);
        Assert.AreEqual("match action", clone.Transition);
        Assert.AreEqual("performer", clone.AdditionalData!["continuity"].GetProperty("subject").GetString());
        Assert.AreEqual(0.92D, clone.AdditionalData["score"].GetDouble());
    }

    [TestMethod]
    public void CloneScene_ReplacesEditableStoryboardContractTogether()
    {
        var source = new PlanSceneDto
        {
            Prompt = "Original",
            Setting = "old setting",
            ShotType = "old shot type",
            CharacterLock = "old character lock",
            StyleLock = "old style lock",
            StartState = "old start state",
            EndState = "old end state",
            Subject = "old subject",
            Action = "old action",
            Camera = "old camera",
            Motion = "old motion",
            EnvironmentMotion = "old environment",
            ContinuityNote = "old continuity",
            Transition = "old transition",
        };

        PlanSceneDto clone = WorkspaceModelHelpers.CloneScene(
            source,
            setting: "flooded conservatory",
            shotType: "medium-wide low angle",
            characterLock: "copper automaton with one blue eye",
            styleLock: "amber-teal oxidized oil paint",
            startState: "left hand lowered beside the window",
            endState: "left hand touching the latch",
            subject: "same copper automaton",
            action: "raises its hand",
            camera: "measured tracking move",
            motion: "head and hand movement",
            environmentMotion: "orchids sway",
            continuity: "preserve the blue eye",
            transition: "match action",
            replaceStoryboardFields: true);

        Assert.AreEqual("flooded conservatory", clone.Setting);
        Assert.AreEqual("medium-wide low angle", clone.ShotType);
        Assert.AreEqual("copper automaton with one blue eye", clone.CharacterLock);
        Assert.AreEqual("amber-teal oxidized oil paint", clone.StyleLock);
        Assert.AreEqual("left hand lowered beside the window", clone.StartState);
        Assert.AreEqual("left hand touching the latch", clone.EndState);
        Assert.AreEqual("same copper automaton", clone.Subject);
        Assert.AreEqual("raises its hand", clone.Action);
        Assert.AreEqual("measured tracking move", clone.Camera);
        Assert.AreEqual("head and hand movement", clone.Motion);
        Assert.AreEqual("orchids sway", clone.EnvironmentMotion);
        Assert.AreEqual("preserve the blue eye", clone.ContinuityNote);
        Assert.AreEqual("match action", clone.Transition);
    }

    [TestMethod]
    public void PlanSceneDto_RoundTripsContinuityFieldsWithCanonicalJsonNames()
    {
        var plan = new PlanDto
        {
            Variants =
            [
                new PlanVariantDto
                {
                    Scenes =
                    [
                        new PlanSceneDto
                        {
                            Prompt = "A continuous reach toward the window latch",
                            Setting = "flooded conservatory",
                            ShotType = "medium-wide low angle",
                            CharacterLock = "copper automaton with one blue eye",
                            StyleLock = "amber-teal oxidized oil paint",
                            StartState = "left hand lowered beside the window",
                            EndState = "left hand touching the latch",
                        },
                    ],
                },
            ],
        };

        string json = JsonSerializer.Serialize(plan, StudioJson.GetTypeInfo<PlanDto>());
        using JsonDocument document = JsonDocument.Parse(json);
        JsonElement sceneJson = document.RootElement
            .GetProperty("variants")[0]
            .GetProperty("scenes")[0];
        Assert.AreEqual("flooded conservatory", sceneJson.GetProperty("setting").GetString());
        Assert.AreEqual("medium-wide low angle", sceneJson.GetProperty("shot_type").GetString());
        Assert.AreEqual("copper automaton with one blue eye", sceneJson.GetProperty("character_lock").GetString());
        Assert.AreEqual("amber-teal oxidized oil paint", sceneJson.GetProperty("style_lock").GetString());
        Assert.AreEqual("left hand lowered beside the window", sceneJson.GetProperty("start_state").GetString());
        Assert.AreEqual("left hand touching the latch", sceneJson.GetProperty("end_state").GetString());

        PlanDto? roundTripped = JsonSerializer.Deserialize(json, StudioJson.GetTypeInfo<PlanDto>());
        PlanSceneDto roundTrippedScene = roundTripped!.Variants[0].Scenes[0];
        Assert.AreEqual(plan.Variants[0].Scenes[0].Setting, roundTrippedScene.Setting);
        Assert.AreEqual(plan.Variants[0].Scenes[0].ShotType, roundTrippedScene.ShotType);
        Assert.AreEqual(plan.Variants[0].Scenes[0].CharacterLock, roundTrippedScene.CharacterLock);
        Assert.AreEqual(plan.Variants[0].Scenes[0].StyleLock, roundTrippedScene.StyleLock);
        Assert.AreEqual(plan.Variants[0].Scenes[0].StartState, roundTrippedScene.StartState);
        Assert.AreEqual(plan.Variants[0].Scenes[0].EndState, roundTrippedScene.EndState);
    }

    [TestMethod]
    public void NormalizeStoryboardContinuity_LocksIdentityStyleAndExactBoundaryState()
    {
        PlanSceneDto[] scenes =
        [
            new PlanSceneDto
            {
                Prompt = "The automaton crosses the flooded conservatory.",
                CharacterLock = "copper automaton with one blue eye",
                StyleLock = "amber-teal oxidized oil paint",
                StartState = "automaton enters from screen left",
                EndState = "automaton reaches the center window facing screen right",
            },
            new PlanSceneDto
            {
                Prompt = "The automaton reaches toward the latch.",
                CharacterLock = "conflicting replacement character",
                StyleLock = "conflicting replacement style",
                StartState = "conflicting reset pose",
                EndState = "automaton touches the latch while facing screen right",
            },
        ];

        IReadOnlyList<PlanSceneDto> normalized =
            WorkspaceModelHelpers.NormalizeStoryboardContinuity(scenes);

        Assert.AreEqual(scenes[0].CharacterLock, normalized[1].CharacterLock);
        Assert.AreEqual(scenes[0].StyleLock, normalized[1].StyleLock);
        Assert.AreEqual(normalized[0].EndState, normalized[1].StartState);
        Assert.AreEqual(scenes[1].EndState, normalized[1].EndState);
    }

    [TestMethod]
    public void NormalizeStoryboardContinuity_PropagatesLockEditedFromAnyScene()
    {
        PlanSceneDto[] scenes =
        [
            new PlanSceneDto
            {
                Prompt = "Character lock: original automaton. Style lock: original nocturnal style. Start state: original automaton enters from screen left. End state: original automaton reaches frame center.",
                CharacterLock = "original automaton",
                StyleLock = "original nocturnal style",
                StartState = "automaton enters from screen left",
                EndState = "original automaton reaches frame center",
            },
            new PlanSceneDto
            {
                CharacterLock = "edited automaton carrying the white orchid",
                StyleLock = "edited copper and moonlit-blue realism",
                StartState = "stale reset pose",
                EndState = "automaton reaches the west exit",
            },
        ];

        IReadOnlyList<PlanSceneDto> normalized =
            WorkspaceModelHelpers.NormalizeStoryboardContinuity(
                scenes,
                scenes[1].CharacterLock,
                scenes[1].StyleLock);

        Assert.IsTrue(normalized.All(scene => scene.CharacterLock == scenes[1].CharacterLock));
        Assert.IsTrue(normalized.All(scene => scene.StyleLock == scenes[1].StyleLock));
        Assert.AreEqual(normalized[0].EndState, normalized[1].StartState);
        Assert.IsFalse(normalized[0].Prompt.Contains("original automaton", StringComparison.Ordinal));
        Assert.IsFalse(normalized[0].Prompt.Contains("original nocturnal style", StringComparison.Ordinal));
        Assert.IsTrue(normalized[0].Prompt.Contains(scenes[1].CharacterLock!, StringComparison.Ordinal));
        Assert.IsTrue(normalized[0].Prompt.Contains(scenes[1].StyleLock!, StringComparison.Ordinal));
        Assert.IsTrue(normalized[0].EndState!.Contains(scenes[1].CharacterLock!, StringComparison.Ordinal));
    }

    [TestMethod]
    public void SceneCurationHelpers_ApplyApprovalLockAndRepairSemantics()
    {
        var original = new PlanSceneDto
        {
            Prompt = "Original",
            AdditionalData = new Dictionary<string, JsonElement>
            {
                ["continuity_id"] = JsonDocument.Parse("\"scene-a\"").RootElement.Clone(),
            },
        };

        var approved = WorkspaceModelHelpers.SetSceneApproval(original, approved: true);
        Assert.IsTrue(WorkspaceModelHelpers.IsSceneApproved(approved));
        Assert.AreEqual("approved", WorkspaceModelHelpers.GetSceneStatus(approved));
        Assert.IsFalse(WorkspaceModelHelpers.IsSceneApproved(original));

        var unapproved = WorkspaceModelHelpers.SetSceneApproval(approved, approved: false);
        Assert.IsFalse(WorkspaceModelHelpers.IsSceneApproved(unapproved));
        Assert.AreEqual("draft", WorkspaceModelHelpers.GetSceneStatus(unapproved));

        var locked = WorkspaceModelHelpers.SetSceneLocked(unapproved, locked: true);
        Assert.IsTrue(WorkspaceModelHelpers.IsSceneLocked(locked));
        Assert.AreEqual("scene-a", locked.AdditionalData!["continuity_id"].GetString());

        var needsRepair = WorkspaceModelHelpers.MarkSceneNeedsRepair(locked);
        Assert.IsFalse(WorkspaceModelHelpers.IsSceneApproved(needsRepair));
        Assert.IsTrue(WorkspaceModelHelpers.IsSceneLocked(needsRepair));
        Assert.AreEqual("needs-repair", WorkspaceModelHelpers.GetSceneStatus(needsRepair));
    }

    [TestMethod]
    public void ParseTemplatePackage_AcceptsBackendSchemaAndPreservesPayload()
    {
        var package = WorkspaceModelHelpers.ParseTemplatePackage(
            """{"schema_version":1,"payload":{"visual_dna":{"palette":"amber"},"render_preset":"quality"},"name":"Reusable look"}""");

        Assert.AreEqual(1, package.SchemaVersion);
        Assert.AreEqual("amber", package.Payload.GetProperty("visual_dna").GetProperty("palette").GetString());
        Assert.AreEqual("Reusable look", package.AdditionalData!["name"].GetString());
    }

    [TestMethod]
    public void ParseTemplatePackage_RejectsUnsupportedOrEmptyPackages()
    {
        Assert.ThrowsExactly<JsonException>(() =>
            WorkspaceModelHelpers.ParseTemplatePackage("""{"schema_version":2,"payload":{"visual_dna":{}}}"""));
        Assert.ThrowsExactly<JsonException>(() =>
            WorkspaceModelHelpers.ParseTemplatePackage("""{"schema_version":1,"payload":{"unknown":true}}"""));
        Assert.ThrowsExactly<JsonException>(() =>
            WorkspaceModelHelpers.ParseTemplatePackage("""{"schema_version":1,"payload":[]}"""));
    }

    [TestMethod]
    public void LiveContextContracts_NormalizeExplicitNullCollections()
    {
        var graph = JsonSerializer.Deserialize(
            """{"timebase":null,"tempo":null,"beats":null,"sections":null,"stems":null,"confidenceNotes":null}""",
            StudioJsonContext.Default.MusicGraphResponse);
        var cues = JsonSerializer.Deserialize(
            """{"events":null,"notes":null}""",
            StudioJsonContext.Default.LiveCuesResponse);
        var assets = JsonSerializer.Deserialize(
            """{"packs":null}""",
            StudioJsonContext.Default.LiveAssetsResponse);

        Assert.IsNotNull(graph);
        Assert.IsNotNull(graph.Timebase);
        Assert.IsNotNull(graph.Tempo);
        Assert.HasCount(0, graph.Beats);
        Assert.HasCount(0, graph.Sections);
        Assert.HasCount(0, graph.Stems);
        Assert.HasCount(0, graph.ConfidenceNotes);
        Assert.IsNotNull(cues);
        Assert.HasCount(0, cues.Events);
        Assert.HasCount(0, cues.Notes);
        Assert.IsNotNull(assets);
        Assert.HasCount(0, assets.Packs);
    }
}
