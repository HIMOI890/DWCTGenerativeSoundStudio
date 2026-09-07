using System.Text.Json;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class ProjectModelTests
{
    [TestMethod]
    public void ProjectRevisionMetadata_DeserializesFromCanonicalFields()
    {
        var project = JsonSerializer.Deserialize<ProjectDto>(
            """
            {
              "id": "p-revision",
              "name": "Revisioned Project",
              "created_at": "2026-08-12T08:00:00Z",
              "updated_at": "2026-08-12T09:30:00Z",
              "revision": 17,
              "schema_version": 1,
              "meta": {}
            }
            """,
            StudioJson.Options)!;

        Assert.AreEqual(17L, project.Revision);
        Assert.AreEqual("2026-08-12T09:30:00Z", project.UpdatedAt);
    }

    [TestMethod]
    public void PersistedProjectMetadata_DrivesNativeWorkflowState()
    {
        var project = JsonSerializer.Deserialize<ProjectDto>(
            """
            {
              "id": "p1",
              "name": "Native Project",
              "created_at": "2026-08-12 08:00:00",
              "schema_version": 1,
              "meta": {
                "audio": { "filename": "track.wav", "size_bytes": 4096 },
                "analysis": {
                  "features": { "bpm": 128, "duration_s": 42.5 },
                  "sections": [{}, {}],
                  "transcript": { "note": "Transcription unavailable; audio features are ready" }
                },
                "last_plan": {
                  "source": "local",
                  "variants": [{ "index": 0, "scenes": [{ "start_s": 0, "end_s": 5, "prompt": "Opening" }] }]
                }
              }
            }
            """,
            StudioJson.Options)!;

        Assert.IsTrue(project.HasAudio);
        Assert.IsTrue(project.HasAnalysis);
        Assert.IsTrue(project.HasPlan);
        Assert.AreEqual("track.wav", project.AudioFileName);
        Assert.AreEqual(4096L, project.AudioSizeBytes);
        Assert.AreEqual(128D, project.Bpm);
        Assert.AreEqual(42.5D, project.DurationSeconds);
        Assert.AreEqual(2, project.SectionCount);
        Assert.AreEqual("Transcription unavailable; audio features are ready", project.TranscriptStatus);
        Assert.AreEqual("Variant 1", project.PlanVariants.Single().DisplayName);
        Assert.AreEqual(1, project.PlanVariants.Single().SceneCount);
    }

    [TestMethod]
    public void LegacyAnalysisShapes_RemainReadableInTheNativeClient()
    {
        var project = JsonSerializer.Deserialize<ProjectDto>(
            """
            {
              "id": "legacy",
              "name": "Legacy Project",
              "created_at": "2026-01-01 00:00:00",
              "schema_version": 1,
              "meta": {
                "analysis": {
                  "duration": 12.75,
                  "features": { "tempo": 96 },
                  "transcript": "legacy transcript"
                }
              }
            }
            """,
            StudioJson.Options)!;

        Assert.AreEqual(96D, project.Bpm);
        Assert.AreEqual(12.75D, project.DurationSeconds);
        Assert.AreEqual("Transcript ready", project.TranscriptStatus);
    }
}
