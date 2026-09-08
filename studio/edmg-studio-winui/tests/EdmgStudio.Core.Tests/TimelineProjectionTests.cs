using System.Text.Json.Nodes;
using EdmgStudio.Core.Models;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class TimelineProjectionTests
{
    [TestMethod]
    public void AudioSplitAndTrim_AdvanceSourceOffsetsWithoutChangingTrackType()
    {
        JsonObject document = JsonNode.Parse("""{"tracks":[{"id":"audio","type":"audio","clips":[{"id":"take","start_s":1,"end_s":5,"data":{"source_in_s":2,"source_out_s":10,"speed":2}}]}]}""")!.AsObject();
        var lane = TimelineProjection.Project(document).Single();
        var (left, right) = TimelineProjection.Split(lane, 3);
        Assert.AreEqual(6d, left.SourceOutSeconds);
        Assert.AreEqual(6d, right.SourceInSeconds);
        var trimmed = TimelineProjection.Trim(lane, 2, 4, 10);
        Assert.AreEqual(4d, trimmed.SourceInSeconds);
        Assert.AreEqual(8d, trimmed.SourceOutSeconds);
        Assert.AreEqual("audio", TimelineProjection.Rebuild(document, [left, right])["tracks"]![0]!["type"]!.GetValue<string>());
    }

    [TestMethod]
    public void CrashRecovery_PrefersDirtyJournalOverNewerSnapshot()
    {
        var recovery = JsonNode.Parse(
            """
            {
              "needs_recovery": true,
              "candidates": [
                {
                  "kind": "snapshot",
                  "path": "snapshot-newest.json",
                  "saved_at": "2026-08-12T12:30:00Z"
                },
                {
                  "kind": "journal",
                  "path": "timeline-journal.json",
                  "saved_at": "2026-08-12T12:00:00Z"
                }
              ]
            }
            """)!.AsObject();

        bool selected = TimelineRecovery.TrySelectCrashRecovery(recovery, out var candidate);

        Assert.IsTrue(selected);
        Assert.AreEqual("journal", candidate.Source);
        Assert.IsNull(candidate.SnapshotName);
    }

    [TestMethod]
    public void CrashRecovery_UsesSnapshotFilenameWhenJournalIsUnavailable()
    {
        var recovery = JsonNode.Parse(
            """
            {
              "needs_recovery": true,
              "candidates": [{
                "kind": "snapshot",
                "path": "C:\\Studio\\snapshots\\snapshot-2026-08-12.json"
              }]
            }
            """)!.AsObject();

        bool selected = TimelineRecovery.TrySelectCrashRecovery(recovery, out var candidate);

        Assert.IsTrue(selected);
        Assert.AreEqual("snapshot", candidate.Source);
        Assert.AreEqual("snapshot-2026-08-12.json", candidate.SnapshotName);
    }

    [TestMethod]
    public void CrashRecovery_DoesNotRestoreCleanBackups()
    {
        var recovery = JsonNode.Parse(
            """
            {
              "needs_recovery": false,
              "candidates": [{"kind": "snapshot", "path": "snapshot.json"}]
            }
            """)!.AsObject();

        Assert.IsFalse(TimelineRecovery.TrySelectCrashRecovery(recovery, out _));
    }

    [TestMethod]
    public void TrackProjection_RebuildPreservesTimelineTrackClipAndDataMetadata()
    {
        var timeline = JsonNode.Parse(
            """
            {
              "version": 7,
              "editor": {"snap": true},
              "tracks": [{
                "id": "track-video",
                "name": "Picture",
                "type": "video",
                "locked": true,
                "clips": [{
                  "id": "clip-1",
                  "start_s": 2.0,
                  "end_s": 8.0,
                  "source_path": "outputs/videos/source.mp4",
                  "source_in_s": 1.5,
                  "source_out_s": 7.5,
                  "speed": 1.25,
                  "volume": 0.7,
                  "muted": false,
                  "fade_in_s": 0.25,
                  "fade_out_s": 0.5,
                  "vendor": {"keep": "clip"},
                  "data": {
                    "name": "Opening",
                    "source_path": "outputs/videos/source.mp4",
                    "custom": {"keep": "data"}
                  }
                }]
              }]
            }
            """)!.AsObject();

        var lanes = TimelineProjection.Project(timeline);
        Assert.HasCount(1, lanes);
        lanes[0].Name = "Opening revised";
        lanes[0].StartSeconds = 3;
        lanes[0].EndSeconds = 9;

        var rebuilt = TimelineProjection.Rebuild(timeline, lanes);
        var track = rebuilt["tracks"]![0]!.AsObject();
        var clip = track["clips"]![0]!.AsObject();

        Assert.AreEqual(7, rebuilt["version"]!.GetValue<int>());
        Assert.IsTrue(rebuilt["editor"]!["snap"]!.GetValue<bool>());
        Assert.IsTrue(track["locked"]!.GetValue<bool>());
        Assert.AreEqual("Opening revised", clip["data"]!["name"]!.GetValue<string>());
        Assert.AreEqual("data", clip["data"]!["custom"]!["keep"]!.GetValue<string>());
        Assert.AreEqual("clip", clip["vendor"]!["keep"]!.GetValue<string>());
        Assert.AreEqual(1.5, clip["source_in_s"]!.GetValue<double>());
        Assert.AreEqual(7.5, clip["source_out_s"]!.GetValue<double>());
        Assert.AreEqual(1.25, clip["speed"]!.GetValue<double>());
        Assert.AreEqual(0.7, clip["volume"]!.GetValue<double>());
        Assert.IsFalse(clip["muted"]!.GetValue<bool>());
        Assert.AreEqual(0.25, clip["fade_in_s"]!.GetValue<double>());
        Assert.AreEqual(0.5, clip["fade_out_s"]!.GetValue<double>());
        Assert.AreEqual(3.0, clip["start_s"]!.GetValue<double>());
        Assert.AreEqual(9.0, clip["end_s"]!.GetValue<double>());
        Assert.IsTrue(TimelineProjection.HasRenderableVideoClip(rebuilt));
    }

    [TestMethod]
    public void TrackLock_CanBeQueriedAndPersistentlyChanged()
    {
        var timeline = CreateVideoTimeline();

        Assert.IsFalse(TimelineProjection.IsTrackLocked(timeline, 0));
        var locked = TimelineProjection.SetTrackLocked(timeline, 0, true);

        Assert.IsTrue(TimelineProjection.IsTrackLocked(locked, 0));
        Assert.IsFalse(TimelineProjection.IsTrackLocked(timeline, 0));
    }

    [TestMethod]
    public void PreserveLockedTracks_RestoresLockedTrackByIdAndKeepsOtherBackendChanges()
    {
        var original = JsonNode.Parse(
            """
            {"revision":"old","tracks":[
              {"id":"locked","locked":true,"clips":[{"id":"keep","start_s":1,"end_s":3}]},
              {"id":"open","clips":[{"id":"old","start_s":0,"end_s":1}]}
            ]}
            """)!.AsObject();
        var proposed = JsonNode.Parse(
            """
            {"revision":"new","tracks":[
              {"id":"open","clips":[{"id":"new","start_s":2,"end_s":4}]},
              {"id":"locked","clips":[{"id":"replace","start_s":5,"end_s":8}]}
            ]}
            """)!.AsObject();

        JsonObject result = TimelineProjection.PreserveLockedTracks(original, proposed);

        Assert.AreEqual("new", result["revision"]!.GetValue<string>());
        Assert.AreEqual("new", result["tracks"]![0]!["clips"]![0]!["id"]!.GetValue<string>());
        Assert.AreEqual("keep", result["tracks"]![1]!["clips"]![0]!["id"]!.GetValue<string>());
        Assert.IsTrue(result["tracks"]![1]!["locked"]!.GetValue<bool>());
    }

    [TestMethod]
    public void RippleAfterDelete_ClosesGapOnlyOnSameTrack()
    {
        var timeline = JsonNode.Parse(
            """
            {"tracks":[
              {"id":"a","clips":[
                {"id":"one","start_s":1,"end_s":3},
                {"id":"two","start_s":4,"end_s":6}
              ]},
              {"id":"b","clips":[{"id":"other","start_s":4,"end_s":6}]}
            ]}
            """)!.AsObject();
        var lanes = TimelineProjection.Project(timeline);
        var deleted = lanes.Single(lane => lane.StableId == "one");
        var remaining = lanes.Where(lane => lane.StableId != deleted.StableId);

        var rippled = TimelineProjection.RippleAfterDelete(remaining, deleted, 10);

        Assert.AreEqual(2, rippled.Single(lane => lane.StableId == "two").StartSeconds);
        Assert.AreEqual(4, rippled.Single(lane => lane.TrackIndex == 1).StartSeconds);
    }

    [TestMethod]
    public void RippleAfterEdit_ShiftsDownstreamByEndDeltaAndKeepsBounds()
    {
        var timeline = JsonNode.Parse(
            """
            {"tracks":[{"id":"a","clips":[
              {"id":"one","start_s":1,"end_s":3},
              {"id":"two","start_s":4,"end_s":6},
              {"id":"three","start_s":8,"end_s":10}
            ]}]}
            """)!.AsObject();
        var lanes = TimelineProjection.Project(timeline);
        var original = lanes[0];
        var edited = TimelineProjection.Trim(original, 1, 5, 10);
        var withEdited = lanes.Select(lane => lane.StableId == original.StableId ? edited : lane);

        var rippled = TimelineProjection.RippleAfterEdit(withEdited, original, edited, 10);

        Assert.AreEqual(6, rippled[1].StartSeconds);
        Assert.AreEqual(8, rippled[2].StartSeconds);
        Assert.AreEqual(10, rippled[2].EndSeconds);
    }

    [TestMethod]
    public void LegacyLayers_RebuildPreservesMetadataAndSupportsDeletion()
    {
        var timeline = JsonNode.Parse(
            """
            {
              "layers": [
                {"id":"one","name":"One","type":"video","start_s":0,"end_s":4,"custom":1},
                {"id":"two","name":"Two","type":"audio","start_s":4,"end_s":8,"custom":2}
              ]
            }
            """)!.AsObject();

        var lanes = TimelineProjection.Project(timeline);
        var rebuilt = TimelineProjection.Rebuild(timeline, lanes.Take(1));
        var layers = rebuilt["layers"]!.AsArray();

        Assert.HasCount(1, layers);
        Assert.AreEqual("one", layers[0]!["id"]!.GetValue<string>());
        Assert.AreEqual(1, layers[0]!["custom"]!.GetValue<int>());
    }

    [TestMethod]
    public void TracksAndLayers_ProjectAndRebuildTogether()
    {
        var timeline = JsonNode.Parse(
            """
            {
              "tracks": [{
                "id": "track",
                "type": "video",
                "clips": [{"id":"clip","name":"Clip","start_s":0,"end_s":4,"custom":"clip"}]
              }],
              "layers": [
                {"id":"overlay","name":"Overlay","type":"text","start_s":1,"end_s":3,"custom":"layer"}
              ],
              "unrelated": {"keep": true}
            }
            """)!.AsObject();

        var lanes = TimelineProjection.Project(timeline);

        Assert.HasCount(2, lanes);
        Assert.IsFalse(lanes[0].IsLayer);
        Assert.IsTrue(lanes[1].IsLayer);
        lanes[0].Name = "Clip edited";
        lanes[1].Name = "Overlay edited";
        var rebuilt = TimelineProjection.Rebuild(timeline, lanes);

        Assert.AreEqual(
            "Clip edited",
            rebuilt["tracks"]![0]!["clips"]![0]!["data"]!["name"]!.GetValue<string>());
        Assert.AreEqual(
            "Overlay edited",
            rebuilt["layers"]![0]!["name"]!.GetValue<string>());
        Assert.AreEqual(
            "layer",
            rebuilt["layers"]![0]!["custom"]!.GetValue<string>());
        Assert.IsTrue(rebuilt["unrelated"]!["keep"]!.GetValue<bool>());
    }

    [TestMethod]
    public void CreateLayer_UsesLegacyCollectionWithoutChangingTracks()
    {
        var timeline = CreateVideoTimeline();
        var trackLane = TimelineProjection.Project(timeline).Single();
        var layer = TimelineProjection.CreateLayer("Caption", "text", 2, 4);

        var rebuilt = TimelineProjection.Rebuild(timeline, [trackLane, layer]);

        Assert.HasCount(1, rebuilt["tracks"]!.AsArray());
        Assert.HasCount(1, rebuilt["layers"]!.AsArray());
        Assert.AreEqual("Caption", rebuilt["layers"]![0]!["name"]!.GetValue<string>());
        Assert.AreEqual("text", rebuilt["layers"]![0]!["type"]!.GetValue<string>());
    }

    [TestMethod]
    public void SourceBackedLayer_RebuildsVideoAdjustmentsInsideData()
    {
        var timeline = CreateVideoTimeline();
        var trackLane = TimelineProjection.Project(timeline).Single();
        var layer = TimelineProjection.CreateLayer("Artwork", "overlay", 1, 3);
        layer.SourcePath = "outputs/images/artwork.png";
        layer.SourceInSeconds = 0;
        layer.SourceOutSeconds = 2;
        layer.FitMode = "contain";
        layer.Opacity = 0.7;
        layer.Brightness = 0.1;
        layer.Contrast = 1.2;
        layer.Saturation = 0.8;
        layer.RotationDegrees = 90;
        layer.FlipHorizontal = true;

        var rebuilt = TimelineProjection.Rebuild(timeline, [trackLane, layer]);
        var rebuiltLayer = rebuilt["layers"]![0]!.AsObject();
        var data = rebuiltLayer["data"]!.AsObject();

        Assert.AreEqual("Artwork", rebuiltLayer["name"]!.GetValue<string>());
        Assert.AreEqual("overlay", rebuiltLayer["type"]!.GetValue<string>());
        Assert.AreEqual(1, rebuiltLayer["start_s"]!.GetValue<double>());
        Assert.AreEqual(3, rebuiltLayer["end_s"]!.GetValue<double>());
        Assert.AreEqual("outputs/images/artwork.png", data["source_path"]!.GetValue<string>());
        Assert.AreEqual("contain", data["fit_mode"]!.GetValue<string>());
        Assert.AreEqual(0.7, data["opacity"]!.GetValue<double>());
        Assert.AreEqual(0.1, data["brightness"]!.GetValue<double>());
        Assert.AreEqual(1.2, data["contrast"]!.GetValue<double>());
        Assert.AreEqual(0.8, data["saturation"]!.GetValue<double>());
        Assert.AreEqual(90, data["rotation_deg"]!.GetValue<int>());
        Assert.IsTrue(data["flip_horizontal"]!.GetValue<bool>());
    }

    [TestMethod]
    public void ReassignTrack_LeavesLegacyLayerInLayerCollection()
    {
        var timeline = JsonNode.Parse(
            """
            {
              "tracks": [{"id":"track","type":"video","clips":[]}],
              "layers": [{"id":"overlay","name":"Overlay","type":"text","start_s":1,"end_s":3}]
            }
            """)!.AsObject();
        var layer = TimelineProjection.Project(timeline).Single(lane => lane.IsLayer);

        var reassigned = TimelineProjection.ReassignTrack(layer, 4);
        var rebuilt = TimelineProjection.Rebuild(timeline, [reassigned]);

        Assert.IsTrue(reassigned.IsLayer);
        Assert.AreEqual(-1, reassigned.TrackIndex);
        Assert.HasCount(1, rebuilt["tracks"]!.AsArray());
        Assert.HasCount(1, rebuilt["layers"]!.AsArray());
        Assert.AreEqual("overlay", rebuilt["layers"]![0]!["id"]!.GetValue<string>());
    }

    [TestMethod]
    public void Rebuild_PersistsEditedLaneTypeAndPreservesTrackMetadata()
    {
        var timeline = CreateVideoTimeline();
        var lane = TimelineProjection.Project(timeline).Single();
        lane.Type = "audio";

        var rebuilt = TimelineProjection.Rebuild(timeline, [lane]);
        var track = rebuilt["tracks"]![0]!.AsObject();
        var projectedAgain = TimelineProjection.Project(rebuilt).Single();

        Assert.AreEqual("audio", track["type"]!.GetValue<string>());
        Assert.AreEqual("video", track["id"]!.GetValue<string>());
        Assert.AreEqual("audio", projectedAgain.Type);
        Assert.AreEqual("keep-me", track["clips"]![0]!["data"]!["custom"]!.GetValue<string>());
    }

    [TestMethod]
    public void Rebuild_SplitsMixedEditedTypesIntoDeterministicTracks()
    {
        var timeline = CreateVideoTimeline();
        var first = TimelineProjection.Project(timeline).Single();
        var second = TimelineProjection.DuplicateAt(first, 6, 12);
        second.Type = "audio";

        var rebuilt = TimelineProjection.Rebuild(timeline, [second, first]);
        var projectedAgain = TimelineProjection.Project(rebuilt);

        Assert.HasCount(2, rebuilt["tracks"]!.AsArray());
        Assert.HasCount(2, projectedAgain);
        Assert.AreEqual("video", rebuilt["tracks"]![0]!["type"]!.GetValue<string>());
        Assert.AreEqual("audio", rebuilt["tracks"]![1]!["type"]!.GetValue<string>());
        CollectionAssert.AreEquivalent(
            new[] { "video", "audio" },
            projectedAgain.Select(lane => lane.Type).ToArray());
    }

    [TestMethod]
    public void EmptyTimeline_NewLaneUsesCanonicalTrackModel()
    {
        var timeline = new JsonObject { ["revision"] = "keep" };
        var lane = TimelineProjection.CreateLane("Generated clip", "video", 1, 5);

        var rebuilt = TimelineProjection.Rebuild(timeline, [lane]);

        Assert.IsNull(rebuilt["layers"]);
        Assert.AreEqual("keep", rebuilt["revision"]!.GetValue<string>());
        var track = rebuilt["tracks"]![0]!.AsObject();
        Assert.AreEqual("video", track["type"]!.GetValue<string>());
        Assert.AreEqual("Generated clip", track["clips"]![0]!["data"]!["name"]!.GetValue<string>());
    }

    [TestMethod]
    public void InvalidLaneTimes_AreRejected()
    {
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(
            () => TimelineProjection.CreateLane("Invalid", "video", 2, 2));
    }

    [TestMethod]
    public void Move_PreservesDurationAndClampsToTimeline()
    {
        var moved = TimelineProjection.Move(GetVideoLane(), 9.5, 10);

        Assert.AreEqual(6, moved.StartSeconds);
        Assert.AreEqual(10, moved.EndSeconds);
        Assert.AreEqual(1, moved.SourceInSeconds);
        Assert.AreEqual(9, moved.SourceOutSeconds);
    }

    [TestMethod]
    public void Trim_AdjustsVideoSourceRangeUsingSpeed()
    {
        var trimmed = TimelineProjection.Trim(GetVideoLane(), 2, 4, 10);

        Assert.AreEqual(2, trimmed.StartSeconds);
        Assert.AreEqual(4, trimmed.EndSeconds);
        Assert.AreEqual(3, trimmed.SourceInSeconds);
        Assert.AreEqual(7, trimmed.SourceOutSeconds);
    }

    [TestMethod]
    public void CanSplitAt_RequiresMinimumDurationOnBothSides()
    {
        TimelineLaneDocument lane = GetVideoLane();

        Assert.IsFalse(TimelineProjection.CanSplitAt(lane, 1.05));
        Assert.IsTrue(TimelineProjection.CanSplitAt(lane, 3));
        Assert.IsFalse(TimelineProjection.CanSplitAt(lane, 4.95));
    }

    [TestMethod]
    public void Split_CreatesIndependentClipsAndDividesSourceRange()
    {
        var (left, right) = TimelineProjection.Split(GetVideoLane(), 3);

        Assert.AreEqual(3, left.EndSeconds);
        Assert.AreEqual(3, right.StartSeconds);
        Assert.AreEqual(5, left.SourceOutSeconds);
        Assert.AreEqual(5, right.SourceInSeconds);
        Assert.AreNotEqual(left.StableId, right.StableId);
    }

    [TestMethod]
    public void Split_NonVideoLanePreservesSourceRangeAndTrack()
    {
        TimelineLaneDocument lane = TimelineProjection.CreateLayer("Caption", "text", 1, 5);
        lane.SourcePath = "caption.json";
        lane.SourceInSeconds = 0.5;
        lane.SourceOutSeconds = 4.5;

        var (left, right) = TimelineProjection.Split(lane, 3);

        Assert.IsTrue(left.IsLayer);
        Assert.IsTrue(right.IsLayer);
        Assert.AreEqual(-1, left.TrackIndex);
        Assert.AreEqual(-1, right.TrackIndex);
        Assert.AreEqual(0.5, left.SourceInSeconds);
        Assert.AreEqual(4.5, left.SourceOutSeconds);
        Assert.AreEqual(0.5, right.SourceInSeconds);
        Assert.AreEqual(4.5, right.SourceOutSeconds);
    }

    [TestMethod]
    public void DuplicateAt_CreatesNewIdentityAtPlayhead()
    {
        var lane = GetVideoLane();
        var duplicate = TimelineProjection.DuplicateAt(lane, 8, 10);

        Assert.AreEqual(6, duplicate.StartSeconds);
        Assert.AreEqual(10, duplicate.EndSeconds);
        Assert.AreNotEqual(lane.StableId, duplicate.StableId);
    }

    [TestMethod]
    public void Rebuild_PersistsInspectorFieldsWithoutLosingUnknownMetadata()
    {
        var source = CreateVideoTimeline();
        var lane = TimelineProjection.Project(source).Single();
        lane.SourcePath = @"C:\media\replacement.mp4";
        lane.SourceInSeconds = 2.5;
        lane.SourceOutSeconds = 7.5;
        lane.Speed = 1.5;
        lane.Volume = 0.75;
        lane.Muted = true;
        lane.FadeInSeconds = 0.2;
        lane.FadeOutSeconds = 0.4;
        lane.FitMode = "cover";
        lane.Opacity = 0.8;
        lane.Brightness = 0.1;
        lane.Contrast = 1.25;
        lane.Saturation = 1.5;
        lane.RotationDegrees = 90;
        lane.FlipHorizontal = true;

        var rebuilt = TimelineProjection.Rebuild(source, [lane]);
        var data = rebuilt["tracks"]![0]!["clips"]![0]!["data"]!.AsObject();

        Assert.AreEqual(@"C:\media\replacement.mp4", data["source_path"]!.GetValue<string>());
        Assert.AreEqual(2.5, data["source_in_s"]!.GetValue<double>());
        Assert.AreEqual(7.5, data["source_out_s"]!.GetValue<double>());
        Assert.AreEqual(1.5, data["speed"]!.GetValue<double>());
        Assert.AreEqual(0.75, data["volume"]!.GetValue<double>());
        Assert.IsTrue(data["muted"]!.GetValue<bool>());
        Assert.AreEqual(0.2, data["fade_in_s"]!.GetValue<double>());
        Assert.AreEqual(0.4, data["fade_out_s"]!.GetValue<double>());
        Assert.AreEqual("cover", data["fit_mode"]!.GetValue<string>());
        Assert.AreEqual(0.8, data["opacity"]!.GetValue<double>());
        Assert.AreEqual(0.1, data["brightness"]!.GetValue<double>());
        Assert.AreEqual(1.25, data["contrast"]!.GetValue<double>());
        Assert.AreEqual(1.5, data["saturation"]!.GetValue<double>());
        Assert.AreEqual(90, data["rotation_deg"]!.GetValue<int>());
        Assert.IsTrue(data["flip_horizontal"]!.GetValue<bool>());
        Assert.AreEqual("keep-me", data["custom"]!.GetValue<string>());
        Assert.AreEqual("root-metadata", rebuilt["custom_root"]!.GetValue<string>());
    }

    [TestMethod]
    public void Rebuild_NormalizesVideoAdjustmentBounds()
    {
        var source = CreateVideoTimeline();
        var lane = TimelineProjection.Project(source).Single();
        lane.FitMode = "unsupported";
        lane.Opacity = 5;
        lane.Brightness = -4;
        lane.Contrast = 8;
        lane.Saturation = 9;
        lane.RotationDegrees = 45;

        var rebuilt = TimelineProjection.Rebuild(source, [lane]);
        var data = rebuilt["tracks"]![0]!["clips"]![0]!["data"]!.AsObject();

        Assert.AreEqual("contain", data["fit_mode"]!.GetValue<string>());
        Assert.AreEqual(1, data["opacity"]!.GetValue<double>());
        Assert.AreEqual(-1, data["brightness"]!.GetValue<double>());
        Assert.AreEqual(2, data["contrast"]!.GetValue<double>());
        Assert.AreEqual(3, data["saturation"]!.GetValue<double>());
        Assert.AreEqual(0, data["rotation_deg"]!.GetValue<int>());
    }

    [TestMethod]
    public void Rebuild_UsesTrackAssignmentAndDeterministicOrder()
    {
        var source = CreateVideoTimeline();
        var first = TimelineProjection.Project(source).Single();
        var later = TimelineProjection.ReassignTrack(
            TimelineProjection.DuplicateAt(first, 5, 12),
            1);
        var earlier = TimelineProjection.ReassignTrack(
            TimelineProjection.DuplicateAt(first, 2, 12),
            1);

        var rebuilt = TimelineProjection.Rebuild(
            source,
            TimelineProjection.OrderLanes([later, first, earlier]));

        Assert.HasCount(2, rebuilt["tracks"]!.AsArray());
        var secondTrackClips = rebuilt["tracks"]![1]!["clips"]!.AsArray();
        Assert.HasCount(2, secondTrackClips);
        Assert.AreEqual(2, secondTrackClips[0]!["start_s"]!.GetValue<double>());
        Assert.AreEqual(5, secondTrackClips[1]!["start_s"]!.GetValue<double>());
    }

    private static TimelineLaneDocument GetVideoLane() =>
        TimelineProjection.Project(CreateVideoTimeline()).Single();

    private static JsonObject CreateVideoTimeline() =>
        JsonNode.Parse(
            """
            {
              "duration_s": 12,
              "custom_root": "root-metadata",
              "tracks": [{
                "id": "video",
                "name": "Video",
                "type": "video",
                "clips": [{
                  "id": "clip-a",
                  "name": "Clip A",
                  "start_s": 1,
                  "end_s": 5,
                  "data": {
                    "source_path": "C:\\media\\source.mp4",
                    "source_in_s": 1,
                    "source_out_s": 9,
                    "speed": 2,
                    "volume": 1,
                    "muted": false,
                    "fade_in_s": 0,
                    "fade_out_s": 0,
                    "custom": "keep-me"
                  }
                }]
              }]
            }
            """)!.AsObject();
}
