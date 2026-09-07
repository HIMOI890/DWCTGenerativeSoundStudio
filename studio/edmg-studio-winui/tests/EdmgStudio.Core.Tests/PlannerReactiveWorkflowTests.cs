using System.Text.Json;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class PlannerReactiveWorkflowTests
{
    [TestMethod]
    public void ScheduleDraftRoundTripsWithContinuityAndUnknownFutureFields()
    {
        const string json = """{"variants":[{"name":"Draft","scenes":[],"schedule_draft":{"schema_version":1,"schedule_revision":"draft-7","source_project_revision":7,"summary":{"image_anchors":4},"warnings":["No transcript"],"image_anchors":[{"t":0,"state":{"position":"left"}}],"future_field":{"keep":true}}}]}""";
        var plan = JsonSerializer.Deserialize(json, StudioJson.GetTypeInfo<PlanDto>());
        Assert.IsNotNull(plan);
        var draft = plan.Variants[0].ScheduleDraft;
        Assert.IsNotNull(draft);
        Assert.AreEqual("draft-7", draft.ScheduleRevision);
        Assert.AreEqual(7L, draft.SourceProjectRevision);
        Assert.AreEqual(4, draft.Summary["image_anchors"]);
        string saved = JsonSerializer.Serialize(plan, StudioJson.GetTypeInfo<PlanDto>());
        StringAssert.Contains(saved, "future_field");
        StringAssert.Contains(saved, "position");
    }

    [TestMethod]
    public void PlannerWorkflow_PreservesSupportedModesAndBuildsCreativeSettings()
    {
        Assert.AreEqual("edmg_core", PlannerWorkflow.NormalizeMode(" EDMG_CORE "));
        Assert.AreEqual("auto", PlannerWorkflow.NormalizeMode("unsupported"));

        var style = PlannerWorkflow.BuildStylePreferences(new PlannerCreativeSettings(
            "  dreamlike performance ",
            "neon cyan, deep blacks",
            "",
            "slow reveal",
            "narrative",
            "fluid",
            "Deforum",
            "quality",
            "follow the chorus"));

        StringAssert.Contains(style, "Creative direction: dreamlike performance");
        StringAssert.Contains(style, "Visual DNA: neon cyan, deep blacks");
        StringAssert.Contains(style, "Conductor intent: follow the chorus");
        Assert.IsFalse(style.Contains("Constraints:", StringComparison.Ordinal));
    }

    [TestMethod]
    public void PlannerWorkflow_ValidatesCreativeInputAndLimits()
    {
        var errors = PlannerWorkflow.Validate(new PlanRequest("Project", "", "", 0, 65));

        CollectionAssert.AreEquivalent(
            new[]
            {
                "Variant count must be between 1 and 10.",
                "Maximum scenes must be between 1 and 64.",
                "Add a creative brief, prompt, or style direction before generating.",
            },
            errors.ToArray());
    }

    [TestMethod]
    public void ReactiveWorkflow_ValidatesDuplicatesReordersAndPreservesMetadata()
    {
        var invalid = new ReactiveMapping
        {
            Name = "",
            SourceSignal = "",
            TargetParameter = "",
            Gain = -1,
            Smoothing = 2,
            Threshold = -0.1,
            InputMinimum = 1,
            InputMaximum = 1,
            OutputMinimum = 2,
            OutputMaximum = 1,
        };
        Assert.HasCount(8, ReactiveWorkflow.ValidateMapping(invalid));

        var first = new ReactiveMapping { Id = "first", Name = "Energy" };
        var duplicate = ReactiveWorkflow.Duplicate(first, "copy");
        Assert.AreEqual("copy", duplicate.Id);
        Assert.AreEqual("Energy copy", duplicate.Name);

        var second = new ReactiveMapping { Id = "second", Name = "Beat" };
        var reordered = ReactiveWorkflow.Move([first, second], 0, 1);
        Assert.AreEqual("second", reordered[0].Id);
        Assert.AreEqual("first", reordered[1].Id);

        using var metadataDocument = JsonDocument.Parse("""{"preset":"cinematic","unknown":{"keep":true}}""");
        var merged = ReactiveWorkflow.MergeMappingsIntoMetadata(
            metadataDocument.RootElement,
            [first, second]);
        Assert.AreEqual("cinematic", merged.GetProperty("preset").GetString());
        Assert.IsTrue(merged.GetProperty("unknown").GetProperty("keep").GetBoolean());
        Assert.AreEqual(2, merged.GetProperty("native_mappings").GetArrayLength());
    }

    [TestMethod]
    public void ReactiveWorkflow_RequiresTimelineContentRatherThanMappingsAlone()
    {
        using var metadataDocument = JsonDocument.Parse("""{"native_mappings":[{"id":"one"}]}""");
        var empty = new ReactiveLabApplyRequest { Metadata = metadataDocument.RootElement.Clone() };
        Assert.IsFalse(ReactiveWorkflow.HasMeaningfulPayload(empty));

        using var scheduleDocument = JsonDocument.Parse("""{"strength":"0:(0.5)"}""");
        var scheduled = new ReactiveLabApplyRequest
        {
            Metadata = metadataDocument.RootElement.Clone(),
            Schedules = scheduleDocument.RootElement.Clone(),
        };
        Assert.IsTrue(ReactiveWorkflow.HasMeaningfulPayload(scheduled));
    }

    [TestMethod]
    public void ReactiveMetadata_RoundTripPreservesUnknownRootSettingsAndMappingProperties()
    {
        const string json =
            """
            {
              "source": "react",
              "selected_variant_index": 2,
              "mappings": [
                {
                  "id": "energy",
                  "name": "Energy",
                  "enabled": true,
                  "source_signal": "rms",
                  "target_parameter": "motion.strength",
                  "future_mapping_option": { "mode": "adaptive" }
                }
              ],
              "settings": {
                "name": "Imported",
                "mapping_preset": "cinematic",
                "sensitivity": 1.25,
                "smoothing": 0.7,
                "future_setting": [1, 2, 3]
              },
              "future_metadata": { "keep": true }
            }
            """;

        var metadata = JsonSerializer.Deserialize(json, StudioJsonContext.Default.ReactiveLabMetadata);
        Assert.IsNotNull(metadata);
        Assert.AreEqual("adaptive", metadata.Mappings[0].ExtensionData!["future_mapping_option"].GetProperty("mode").GetString());
        Assert.AreEqual(3, metadata.Settings.ExtensionData!["future_setting"].GetArrayLength());
        Assert.IsTrue(metadata.ExtensionData!["future_metadata"].GetProperty("keep").GetBoolean());

        var roundTrip = JsonSerializer.SerializeToElement(metadata, StudioJsonContext.Default.ReactiveLabMetadata);
        Assert.AreEqual("adaptive", roundTrip.GetProperty("mappings")[0].GetProperty("future_mapping_option").GetProperty("mode").GetString());
        Assert.AreEqual(3, roundTrip.GetProperty("settings").GetProperty("future_setting").GetArrayLength());
        Assert.IsTrue(roundTrip.GetProperty("future_metadata").GetProperty("keep").GetBoolean());
    }

    [TestMethod]
    public void ReactiveLocalState_RoundTripPreservesCurrentPresetAndSavedPresets()
    {
        var state = new ReactiveLabLocalState
        {
            Current = new ReactivePreset
            {
                Name = "Current",
                MappingPreset = "percussive",
                Sensitivity = 1.4,
                Smoothing = 0.65,
                Mappings =
                [
                    new ReactiveMapping
                    {
                        Id = "beat-scale",
                        Name = "Beat scale",
                        SourceSignal = "beat",
                        TargetParameter = "camera.scale",
                        Quantization = "beat",
                        Section = "Chorus",
                        Cue = "drop"
                    }
                ]
            },
            Presets =
            [
                new ReactivePreset { Name = "Saved", RenderMode = "quality", ScheduleStride = 8 }
            ]
        };

        var json = JsonSerializer.Serialize(state, StudioJsonContext.Default.ReactiveLabLocalState);
        var roundTrip = JsonSerializer.Deserialize(json, StudioJsonContext.Default.ReactiveLabLocalState);

        Assert.IsNotNull(roundTrip);
        Assert.AreEqual("percussive", roundTrip.Current.MappingPreset);
        Assert.AreEqual("Chorus", roundTrip.Current.Mappings[0].Section);
        Assert.AreEqual("drop", roundTrip.Current.Mappings[0].Cue);
        Assert.AreEqual("Saved", roundTrip.Presets[0].Name);
        Assert.AreEqual(8, roundTrip.Presets[0].ScheduleStride);
    }
}
