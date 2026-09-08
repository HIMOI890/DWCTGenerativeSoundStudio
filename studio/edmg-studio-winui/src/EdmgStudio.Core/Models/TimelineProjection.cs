using System.Globalization;
using System.Text.Json.Nodes;

namespace EdmgStudio.Core.Models;

public enum TimelineLaneOrigin
{
    Layer,
    TrackClip,
    NewClip
}

public sealed class TimelineLaneDocument
{
    private string _sourcePath;
    private double _sourceInSeconds;
    private double _sourceOutSeconds;
    private double _speed;
    private double _volume;
    private bool _muted;
    private double _fadeInSeconds;
    private double _fadeOutSeconds;
    private string _fitMode;
    private double _opacity;
    private double _brightness;
    private double _contrast;
    private double _saturation;
    private int _rotationDegrees;
    private bool _flipHorizontal;

    internal TimelineLaneDocument(
        string stableId,
        string name,
        string type,
        double startSeconds,
        double endSeconds,
        TimelineLaneOrigin origin,
        int trackIndex,
        JsonObject source,
        string sourcePath = "",
        double sourceInSeconds = 0,
        double sourceOutSeconds = 0,
        double speed = 1,
        double volume = 1,
        bool muted = false,
        double fadeInSeconds = 0,
        double fadeOutSeconds = 0,
        string fitMode = "contain",
        double opacity = 1,
        double brightness = 0,
        double contrast = 1,
        double saturation = 1,
        int rotationDegrees = 0,
        bool flipHorizontal = false,
        IReadOnlySet<string>? presentMediaProperties = null)
    {
        StableId = stableId;
        Name = name;
        Type = type;
        StartSeconds = startSeconds;
        EndSeconds = endSeconds;
        Origin = origin;
        TrackIndex = trackIndex;
        Source = source;
        _sourcePath = sourcePath;
        _sourceInSeconds = sourceInSeconds;
        _sourceOutSeconds = sourceOutSeconds;
        _speed = speed;
        _volume = volume;
        _muted = muted;
        _fadeInSeconds = fadeInSeconds;
        _fadeOutSeconds = fadeOutSeconds;
        _fitMode = NormalizeFitMode(fitMode);
        _opacity = opacity;
        _brightness = brightness;
        _contrast = contrast;
        _saturation = saturation;
        _rotationDegrees = NormalizeRotation(rotationDegrees);
        _flipHorizontal = flipHorizontal;
        PresentMediaProperties = presentMediaProperties is null
            ? new HashSet<string>(StringComparer.Ordinal)
            : new HashSet<string>(presentMediaProperties, StringComparer.Ordinal);
    }

    public string StableId { get; }
    public string Name { get; set; }
    public string Type { get; set; }
    public double StartSeconds { get; set; }
    public double EndSeconds { get; set; }
    public int TrackIndex { get; internal set; }
    public bool IsLayer => Origin == TimelineLaneOrigin.Layer;

    public string SourcePath
    {
        get => _sourcePath;
        set
        {
            _sourcePath = value ?? string.Empty;
            PresentMediaProperties.Add("source_path");
        }
    }

    public double SourceInSeconds
    {
        get => _sourceInSeconds;
        set
        {
            _sourceInSeconds = value;
            PresentMediaProperties.Add("source_in_s");
        }
    }

    public double SourceOutSeconds
    {
        get => _sourceOutSeconds;
        set
        {
            _sourceOutSeconds = value;
            PresentMediaProperties.Add("source_out_s");
        }
    }

    public double Speed
    {
        get => _speed;
        set
        {
            _speed = value;
            PresentMediaProperties.Add("speed");
        }
    }

    public double Volume
    {
        get => _volume;
        set
        {
            _volume = value;
            PresentMediaProperties.Add("volume");
        }
    }

    public bool Muted
    {
        get => _muted;
        set
        {
            _muted = value;
            PresentMediaProperties.Add("muted");
        }
    }

    public double FadeInSeconds
    {
        get => _fadeInSeconds;
        set
        {
            _fadeInSeconds = value;
            PresentMediaProperties.Add("fade_in_s");
        }
    }

    public double FadeOutSeconds
    {
        get => _fadeOutSeconds;
        set
        {
            _fadeOutSeconds = value;
            PresentMediaProperties.Add("fade_out_s");
        }
    }

    public string FitMode
    {
        get => _fitMode;
        set
        {
            _fitMode = NormalizeFitMode(value);
            PresentMediaProperties.Add("fit_mode");
        }
    }

    public double Opacity
    {
        get => _opacity;
        set
        {
            _opacity = value;
            PresentMediaProperties.Add("opacity");
        }
    }

    public double Brightness
    {
        get => _brightness;
        set
        {
            _brightness = value;
            PresentMediaProperties.Add("brightness");
        }
    }

    public double Contrast
    {
        get => _contrast;
        set
        {
            _contrast = value;
            PresentMediaProperties.Add("contrast");
        }
    }

    public double Saturation
    {
        get => _saturation;
        set
        {
            _saturation = value;
            PresentMediaProperties.Add("saturation");
        }
    }

    public int RotationDegrees
    {
        get => _rotationDegrees;
        set
        {
            _rotationDegrees = NormalizeRotation(value);
            PresentMediaProperties.Add("rotation_deg");
        }
    }

    public bool FlipHorizontal
    {
        get => _flipHorizontal;
        set
        {
            _flipHorizontal = value;
            PresentMediaProperties.Add("flip_horizontal");
        }
    }

    internal TimelineLaneOrigin Origin { get; }
    internal JsonObject Source { get; }
    internal HashSet<string> PresentMediaProperties { get; }

    private static string NormalizeFitMode(string? value)
    {
        string normalized = string.IsNullOrWhiteSpace(value)
            ? "contain"
            : value.Trim().ToLowerInvariant();
        return normalized is "contain" or "cover" or "stretch" ? normalized : "contain";
    }

    private static int NormalizeRotation(int value) => value is 90 or 180 or 270 ? value : 0;
}

public static class TimelineProjection
{
    public const double MinimumDurationSeconds = 0.05;

    private static readonly string[] MediaPropertyNames =
    [
        "source_path",
        "source_in_s",
        "source_out_s",
        "speed",
        "volume",
        "muted",
        "fade_in_s",
        "fade_out_s",
        "fit_mode",
        "opacity",
        "brightness",
        "contrast",
        "saturation",
        "rotation_deg",
        "flip_horizontal"
    ];

    public static IReadOnlyList<TimelineLaneDocument> Project(JsonObject timeline)
    {
        ArgumentNullException.ThrowIfNull(timeline);
        var lanes = new List<TimelineLaneDocument>();
        if (timeline["tracks"] is JsonArray tracks)
        {
            lanes.AddRange(ProjectTracks(tracks));
        }

        if (timeline["layers"] is JsonArray layers)
        {
            lanes.AddRange(ProjectLayers(layers));
        }

        return OrderLanes(lanes);
    }

    public static TimelineLaneDocument CreateLane(
        string name,
        string type,
        double startSeconds,
        double endSeconds)
    {
        ValidateTimes(startSeconds, endSeconds);
        var stableId = Guid.NewGuid().ToString("N", CultureInfo.InvariantCulture);
        return new TimelineLaneDocument(
            stableId,
            NormalizeName(name, stableId),
            NormalizeType(type),
            startSeconds,
            endSeconds,
            TimelineLaneOrigin.NewClip,
            -1,
            new JsonObject
            {
                ["id"] = stableId,
                ["start_s"] = startSeconds,
                ["end_s"] = endSeconds,
                ["data"] = new JsonObject
                {
                    ["name"] = NormalizeName(name, stableId)
                }
            });
    }

    public static TimelineLaneDocument CreateLayer(
        string name,
        string type,
        double startSeconds,
        double endSeconds)
    {
        ValidateTimes(startSeconds, endSeconds);
        var stableId = Guid.NewGuid().ToString("N", CultureInfo.InvariantCulture);
        var normalizedName = NormalizeName(name, stableId);
        var normalizedType = NormalizeType(type);
        return new TimelineLaneDocument(
            stableId,
            normalizedName,
            normalizedType,
            startSeconds,
            endSeconds,
            TimelineLaneOrigin.Layer,
            -1,
            new JsonObject
            {
                ["id"] = stableId,
                ["name"] = normalizedName,
                ["type"] = normalizedType,
                ["start_s"] = startSeconds,
                ["end_s"] = endSeconds
            });
    }

    public static JsonObject Rebuild(JsonObject timeline, IEnumerable<TimelineLaneDocument> lanes)
    {
        ArgumentNullException.ThrowIfNull(timeline);
        ArgumentNullException.ThrowIfNull(lanes);
        var materializedLanes = lanes.ToList();
        foreach (var lane in materializedLanes)
        {
            ArgumentNullException.ThrowIfNull(lane);
            ValidateTimes(lane.StartSeconds, lane.EndSeconds);
        }

        var rebuilt = timeline.DeepClone().AsObject();
        var trackLanes = materializedLanes.Where(lane => !lane.IsLayer).ToList();
        var layerLanes = materializedLanes.Where(lane => lane.IsLayer).ToList();
        bool hadTracks = rebuilt["tracks"] is JsonArray;
        bool hadLayers = rebuilt["layers"] is JsonArray;

        if (hadTracks || trackLanes.Count > 0)
        {
            var tracks = rebuilt["tracks"] as JsonArray;
            if (tracks is null)
            {
                tracks = [];
                rebuilt["tracks"] = tracks;
            }

            RebuildTracks(tracks, trackLanes);
        }

        if (hadLayers || layerLanes.Count > 0)
        {
            var layers = rebuilt["layers"] as JsonArray;
            if (layers is null)
            {
                layers = [];
                rebuilt["layers"] = layers;
            }

            RebuildLayers(layers, layerLanes);
        }

        return rebuilt;
    }

    public static bool HasRenderableVideoClip(JsonObject timeline)
    {
        ArgumentNullException.ThrowIfNull(timeline);
        return Project(timeline).Any(lane =>
            lane.Type.Contains("video", StringComparison.OrdinalIgnoreCase) &&
            !string.IsNullOrWhiteSpace(lane.SourcePath));
    }

    public static bool CanRender(TimelineLaneDocument lane) =>
        lane.EndSeconds > lane.StartSeconds &&
        !string.IsNullOrWhiteSpace(lane.SourcePath);

    public static TimelineLaneDocument Move(
        TimelineLaneDocument lane,
        double startSeconds,
        double timelineDurationSeconds)
    {
        ValidateTimelineDuration(timelineDurationSeconds);
        var result = CloneLane(lane);
        var duration = Math.Max(MinimumDurationSeconds, lane.EndSeconds - lane.StartSeconds);
        result.StartSeconds = Math.Clamp(startSeconds, 0, Math.Max(0, timelineDurationSeconds - duration));
        result.EndSeconds = result.StartSeconds + duration;
        return result;
    }

    public static TimelineLaneDocument Trim(
        TimelineLaneDocument lane,
        double startSeconds,
        double endSeconds,
        double timelineDurationSeconds)
    {
        ValidateTimelineDuration(timelineDurationSeconds);
        var start = Math.Clamp(startSeconds, 0, Math.Max(0, timelineDurationSeconds - MinimumDurationSeconds));
        var end = Math.Clamp(endSeconds, start + MinimumDurationSeconds, timelineDurationSeconds);
        var result = CloneLane(lane);
        if (IsTimedMedia(lane))
        {
            var speed = Math.Clamp(lane.Speed, 0.25, 4);
            var sourceIn = Math.Max(0, lane.SourceInSeconds + ((start - lane.StartSeconds) * speed));
            var fallbackSourceOut = lane.SourceInSeconds + ((lane.EndSeconds - lane.StartSeconds) * speed);
            var sourceOut = Math.Max(
                sourceIn + MinimumDurationSeconds,
                Math.Max(lane.SourceOutSeconds, fallbackSourceOut) + ((end - lane.EndSeconds) * speed));
            result.SourceInSeconds = sourceIn;
            result.SourceOutSeconds = sourceOut;
            result.Speed = speed;
        }

        result.StartSeconds = start;
        result.EndSeconds = end;
        return result;
    }

    public static bool CanSplitAt(TimelineLaneDocument lane, double splitSeconds)
    {
        ArgumentNullException.ThrowIfNull(lane);
        return splitSeconds > lane.StartSeconds + MinimumDurationSeconds &&
            splitSeconds < lane.EndSeconds - MinimumDurationSeconds;
    }

    public static (TimelineLaneDocument Left, TimelineLaneDocument Right) Split(
        TimelineLaneDocument lane,
        double splitSeconds)
    {
        if (!CanSplitAt(lane, splitSeconds))
        {
            throw new ArgumentOutOfRangeException(
                nameof(splitSeconds),
                "The split position must leave at least 0.05 seconds on each side.");
        }

        var left = CloneLane(lane);
        var right = CloneLane(lane, createNewIdentity: true);
        left.EndSeconds = splitSeconds;
        right.StartSeconds = splitSeconds;
        if (IsTimedMedia(lane))
        {
            var speed = Math.Clamp(lane.Speed, 0.25, 4);
            var sourceSplit = Math.Max(0, lane.SourceInSeconds + ((splitSeconds - lane.StartSeconds) * speed));
            left.SourceOutSeconds = sourceSplit;
            right.SourceInSeconds = sourceSplit;
            left.Speed = speed;
            right.Speed = speed;
        }

        return (left, right);
    }

    public static TimelineLaneDocument DuplicateAt(
        TimelineLaneDocument lane,
        double playheadSeconds,
        double timelineDurationSeconds)
    {
        ValidateTimelineDuration(timelineDurationSeconds);
        var duplicate = CloneLane(lane, createNewIdentity: true);
        var duration = Math.Max(MinimumDurationSeconds, lane.EndSeconds - lane.StartSeconds);
        duplicate.StartSeconds = Math.Clamp(
            playheadSeconds,
            0,
            Math.Max(0, timelineDurationSeconds - duration));
        duplicate.EndSeconds = duplicate.StartSeconds + duration;
        return duplicate;
    }

    public static TimelineLaneDocument ReassignTrack(TimelineLaneDocument lane, int trackIndex)
    {
        var result = CloneLane(lane);
        if (lane.IsLayer)
        {
            result.TrackIndex = -1;
            return result;
        }

        ArgumentOutOfRangeException.ThrowIfNegative(trackIndex);
        result.TrackIndex = trackIndex;
        return result;
    }

    public static bool IsTrackLocked(JsonObject timeline, int trackIndex)
    {
        ArgumentNullException.ThrowIfNull(timeline);
        ArgumentOutOfRangeException.ThrowIfNegative(trackIndex);
        var tracks = timeline["tracks"] as JsonArray;
        return trackIndex < (tracks?.Count ?? 0) &&
            tracks![trackIndex] is JsonObject track &&
            track["locked"]?.GetValue<bool>() == true;
    }

    public static JsonObject SetTrackLocked(JsonObject timeline, int trackIndex, bool locked)
    {
        ArgumentNullException.ThrowIfNull(timeline);
        ArgumentOutOfRangeException.ThrowIfNegative(trackIndex);
        var result = timeline.DeepClone().AsObject();
        var tracks = result["tracks"] as JsonArray ??
            throw new ArgumentException("The timeline does not contain tracks.", nameof(timeline));
        if (trackIndex >= tracks.Count || tracks[trackIndex] is not JsonObject track)
        {
            throw new ArgumentOutOfRangeException(nameof(trackIndex));
        }

        track["locked"] = locked;
        return result;
    }

    public static JsonObject PreserveLockedTracks(JsonObject original, JsonObject proposed)
    {
        ArgumentNullException.ThrowIfNull(original);
        ArgumentNullException.ThrowIfNull(proposed);
        var result = proposed.DeepClone().AsObject();
        if (original["tracks"] is not JsonArray originalTracks)
        {
            return result;
        }

        var proposedTracks = result["tracks"] as JsonArray ?? [];
        result["tracks"] = proposedTracks;
        for (var originalIndex = 0; originalIndex < originalTracks.Count; originalIndex++)
        {
            if (originalTracks[originalIndex] is not JsonObject originalTrack ||
                originalTrack["locked"]?.GetValue<bool>() != true)
            {
                continue;
            }

            int targetIndex = FindTrackIndex(proposedTracks, originalTrack, originalIndex);
            JsonNode preservedTrack = originalTrack.DeepClone();
            if (targetIndex < proposedTracks.Count)
            {
                proposedTracks[targetIndex] = preservedTrack;
            }
            else
            {
                while (proposedTracks.Count < targetIndex)
                {
                    proposedTracks.Add(new JsonObject { ["clips"] = new JsonArray() });
                }

                proposedTracks.Add(preservedTrack);
            }
        }

        return result;
    }

    public static IReadOnlyList<TimelineLaneDocument> RippleAfterEdit(
        IEnumerable<TimelineLaneDocument> lanes,
        TimelineLaneDocument original,
        TimelineLaneDocument edited,
        double timelineDurationSeconds)
    {
        ArgumentNullException.ThrowIfNull(lanes);
        ArgumentNullException.ThrowIfNull(original);
        ArgumentNullException.ThrowIfNull(edited);
        ValidateTimelineDuration(timelineDurationSeconds);
        if (original.IsLayer || edited.IsLayer || original.TrackIndex != edited.TrackIndex)
        {
            return lanes.Select(lane => CloneLane(lane)).ToArray();
        }

        return Ripple(
            lanes,
            original,
            edited.EndSeconds - original.EndSeconds,
            timelineDurationSeconds);
    }

    public static IReadOnlyList<TimelineLaneDocument> RippleAfterDelete(
        IEnumerable<TimelineLaneDocument> lanes,
        TimelineLaneDocument deleted,
        double timelineDurationSeconds)
    {
        ArgumentNullException.ThrowIfNull(lanes);
        ArgumentNullException.ThrowIfNull(deleted);
        ValidateTimelineDuration(timelineDurationSeconds);
        if (deleted.IsLayer)
        {
            return lanes.Select(lane => CloneLane(lane)).ToArray();
        }

        return Ripple(
            lanes,
            deleted,
            -(deleted.EndSeconds - deleted.StartSeconds),
            timelineDurationSeconds);
    }

    private static IReadOnlyList<TimelineLaneDocument> Ripple(
        IEnumerable<TimelineLaneDocument> lanes,
        TimelineLaneDocument anchor,
        double deltaSeconds,
        double timelineDurationSeconds) =>
        lanes.Select(lane =>
        {
            var result = CloneLane(lane);
            if (lane.IsLayer ||
                lane.TrackIndex != anchor.TrackIndex ||
                lane.StableId == anchor.StableId ||
                lane.StartSeconds < anchor.EndSeconds)
            {
                return result;
            }

            var duration = Math.Max(MinimumDurationSeconds, lane.EndSeconds - lane.StartSeconds);
            result.StartSeconds = Math.Clamp(
                lane.StartSeconds + deltaSeconds,
                0,
                Math.Max(0, timelineDurationSeconds - duration));
            result.EndSeconds = result.StartSeconds + duration;
            return result;
        }).ToArray();

    private static int FindTrackIndex(JsonArray tracks, JsonObject originalTrack, int fallbackIndex)
    {
        string? originalId = GetString(originalTrack["id"]);
        if (!string.IsNullOrWhiteSpace(originalId))
        {
            for (var index = 0; index < tracks.Count; index++)
            {
                if (tracks[index] is JsonObject track &&
                    string.Equals(GetString(track["id"]), originalId, StringComparison.Ordinal))
                {
                    return index;
                }
            }
        }

        return fallbackIndex;
    }

    public static IReadOnlyList<TimelineLaneDocument> OrderLanes(
        IEnumerable<TimelineLaneDocument> lanes) =>
        lanes
            .OrderBy(lane => lane.IsLayer ? 1 : 0)
            .ThenBy(lane => lane.TrackIndex)
            .ThenBy(lane => lane.StartSeconds)
            .ThenBy(lane => lane.EndSeconds)
            .ThenBy(lane => lane.StableId, StringComparer.Ordinal)
            .ToArray();

    private static List<TimelineLaneDocument> ProjectTracks(JsonArray tracks)
    {
        var lanes = new List<TimelineLaneDocument>();
        for (var trackIndex = 0; trackIndex < tracks.Count; trackIndex++)
        {
            if (tracks[trackIndex] is not JsonObject track || track["clips"] is not JsonArray clips)
            {
                continue;
            }

            var trackType = GetString(track["type"]) ?? "video";
            var trackName = GetString(track["name"]);
            for (var clipIndex = 0; clipIndex < clips.Count; clipIndex++)
            {
                if (clips[clipIndex] is not JsonObject clip)
                {
                    continue;
                }

                var data = clip["data"] as JsonObject;
                var id = GetString(clip["id"]) ?? $"track-{trackIndex}-clip-{clipIndex}";
                var start = GetDouble(clip["start_s"], 0);
                var end = GetDouble(clip["end_s"], 1);
                lanes.Add(new TimelineLaneDocument(
                    id,
                    GetString(data?["name"]) ?? GetString(clip["name"]) ?? trackName ?? $"Clip {lanes.Count + 1}",
                    trackType,
                    start,
                    end,
                    TimelineLaneOrigin.TrackClip,
                    trackIndex,
                    clip.DeepClone().AsObject(),
                    GetMediaString(clip, "source_path"),
                    GetMediaDouble(clip, "source_in_s", 0),
                    GetMediaDouble(clip, "source_out_s", Math.Max(0, end - start)),
                    GetMediaDouble(clip, "speed", 1),
                    GetMediaDouble(clip, "volume", 1),
                    GetMediaBoolean(clip, "muted", false),
                    GetMediaDouble(clip, "fade_in_s", 0),
                    GetMediaDouble(clip, "fade_out_s", 0),
                    GetMediaString(clip, "fit_mode", "contain"),
                    GetMediaDouble(clip, "opacity", 1),
                    GetMediaDouble(clip, "brightness", 0),
                    GetMediaDouble(clip, "contrast", 1),
                    GetMediaDouble(clip, "saturation", 1),
                    (int)Math.Round(GetMediaDouble(clip, "rotation_deg", 0)),
                    GetMediaBoolean(clip, "flip_horizontal", false),
                    GetPresentMediaProperties(clip)));
            }
        }

        return lanes;
    }

    private static List<TimelineLaneDocument> ProjectLayers(JsonArray layers)
    {
        var lanes = new List<TimelineLaneDocument>();
        for (var index = 0; index < layers.Count; index++)
        {
            if (layers[index] is not JsonObject layer)
            {
                continue;
            }

            var id = GetString(layer["id"]) ?? $"layer-{index}";
            var start = GetDouble(layer["start_s"], 0);
            var end = GetDouble(layer["end_s"], 1);
            lanes.Add(new TimelineLaneDocument(
                id,
                GetString(layer["name"]) ?? $"Layer {index + 1}",
                GetString(layer["type"]) ?? "video",
                start,
                end,
                TimelineLaneOrigin.Layer,
                -1,
                layer.DeepClone().AsObject(),
                GetMediaString(layer, "source_path"),
                GetMediaDouble(layer, "source_in_s", 0),
                GetMediaDouble(layer, "source_out_s", Math.Max(0, end - start)),
                GetMediaDouble(layer, "speed", 1),
                GetMediaDouble(layer, "volume", 1),
                GetMediaBoolean(layer, "muted", false),
                GetMediaDouble(layer, "fade_in_s", 0),
                GetMediaDouble(layer, "fade_out_s", 0),
                GetMediaString(layer, "fit_mode", "contain"),
                GetMediaDouble(layer, "opacity", 1),
                GetMediaDouble(layer, "brightness", 0),
                GetMediaDouble(layer, "contrast", 1),
                GetMediaDouble(layer, "saturation", 1),
                (int)Math.Round(GetMediaDouble(layer, "rotation_deg", 0)),
                GetMediaBoolean(layer, "flip_horizontal", false),
                GetPresentMediaProperties(layer)));
        }

        return lanes;
    }

    private static void RebuildTracks(JsonArray tracks, IReadOnlyList<TimelineLaneDocument> lanes)
    {
        foreach (var node in tracks)
        {
            if (node is JsonObject track)
            {
                track["clips"] = new JsonArray();
            }
        }

        var orderedLanes = OrderLanes(lanes);
        var assignments = new Dictionary<TimelineLaneDocument, int>();
        var claimedTrackTypes = new Dictionary<int, string>();

        foreach (var requestedTrack in orderedLanes
                     .Where(lane => lane.TrackIndex >= 0)
                     .GroupBy(lane => lane.TrackIndex)
                     .OrderBy(group => group.Key))
        {
            var requestedTrackIndex = requestedTrack.Key;
            var firstLane = requestedTrack.First();
            EnsureTrackIndex(tracks, requestedTrackIndex, firstLane.Type);
            var track = tracks[requestedTrackIndex]!.AsObject();
            var existingType = NormalizeType(GetString(track["type"]));
            var desiredType = requestedTrack
                .Select(lane => NormalizeType(lane.Type))
                .FirstOrDefault(type => string.Equals(type, existingType, StringComparison.OrdinalIgnoreCase))
                ?? NormalizeType(firstLane.Type);

            claimedTrackTypes[requestedTrackIndex] = desiredType;
            track["type"] = desiredType;
            foreach (var lane in requestedTrack.Where(
                         lane => string.Equals(
                             NormalizeType(lane.Type),
                             desiredType,
                             StringComparison.OrdinalIgnoreCase)))
            {
                assignments[lane] = requestedTrackIndex;
            }
        }

        foreach (var lane in orderedLanes.Where(lane => !assignments.ContainsKey(lane)))
        {
            var normalizedType = NormalizeType(lane.Type);
            var trackIndex = FindOrCreateCompatibleTrack(tracks, claimedTrackTypes, normalizedType);
            assignments[lane] = trackIndex;
            claimedTrackTypes[trackIndex] = normalizedType;
            tracks[trackIndex]!.AsObject()["type"] = normalizedType;
        }

        foreach (var group in orderedLanes
                     .GroupBy(lane => assignments[lane])
                     .OrderBy(group => group.Key))
        {
            var track = tracks[group.Key]!.AsObject();
            var clips = track["clips"]!.AsArray();
            foreach (var lane in group
                         .OrderBy(lane => lane.StartSeconds)
                         .ThenBy(lane => lane.EndSeconds)
                         .ThenBy(lane => lane.StableId, StringComparer.Ordinal))
            {
                var clip = BuildLaneNode(lane);
                var data = clip["data"] as JsonObject ?? [];
                data["name"] = NormalizeName(lane.Name, lane.StableId);
                clip["data"] = data;
                clips.Add((JsonNode)clip);
            }
        }
    }

    private static void RebuildLayers(JsonArray layers, IReadOnlyList<TimelineLaneDocument> lanes)
    {
        layers.Clear();
        foreach (var lane in lanes)
        {
            var layer = BuildLaneNode(lane);
            layer["name"] = NormalizeName(lane.Name, lane.StableId);
            layer["type"] = NormalizeType(lane.Type);
            layers.Add((JsonNode)layer);
        }
    }

    private static JsonObject BuildLaneNode(TimelineLaneDocument lane)
    {
        var node = lane.Source.DeepClone().AsObject();
        node["id"] = lane.StableId;
        node["start_s"] = lane.StartSeconds;
        node["end_s"] = lane.EndSeconds;
        WriteMediaProperties(node, lane);
        return node;
    }

    private static TimelineLaneDocument CloneLane(
        TimelineLaneDocument lane,
        bool createNewIdentity = false)
    {
        var stableId = createNewIdentity ? Guid.NewGuid().ToString("N") : lane.StableId;
        return new TimelineLaneDocument(
            stableId,
            lane.Name,
            lane.Type,
            lane.StartSeconds,
            lane.EndSeconds,
            lane.Origin,
            lane.TrackIndex,
            lane.Source.DeepClone().AsObject(),
            lane.SourcePath,
            lane.SourceInSeconds,
            lane.SourceOutSeconds,
            lane.Speed,
            lane.Volume,
            lane.Muted,
            lane.FadeInSeconds,
            lane.FadeOutSeconds,
            lane.FitMode,
            lane.Opacity,
            lane.Brightness,
            lane.Contrast,
            lane.Saturation,
            lane.RotationDegrees,
            lane.FlipHorizontal,
            lane.PresentMediaProperties);
    }

    private static void EnsureTrackIndex(JsonArray tracks, int trackIndex, string type)
    {
        while (tracks.Count <= trackIndex)
        {
            var normalizedType = NormalizeType(type);
            tracks.Add((JsonNode)new JsonObject
            {
                ["id"] = $"track-{Guid.NewGuid():N}",
                ["name"] = normalizedType.Equals("audio", StringComparison.OrdinalIgnoreCase) ? "Audio" : "Video",
                ["type"] = normalizedType,
                ["clips"] = new JsonArray()
            });
        }
    }

    private static int FindOrCreateCompatibleTrack(
        JsonArray tracks,
        IReadOnlyDictionary<int, string> claimedTrackTypes,
        string type)
    {
        var normalizedType = NormalizeType(type);
        for (var index = 0; index < tracks.Count; index++)
        {
            if (claimedTrackTypes.TryGetValue(index, out var claimedType))
            {
                if (string.Equals(claimedType, normalizedType, StringComparison.OrdinalIgnoreCase))
                {
                    return index;
                }

                continue;
            }

            if (tracks[index] is JsonObject track &&
                string.Equals(
                    NormalizeType(GetString(track["type"])),
                    normalizedType,
                    StringComparison.OrdinalIgnoreCase))
            {
                return index;
            }
        }

        EnsureTrackIndex(tracks, tracks.Count, normalizedType);
        return tracks.Count - 1;
    }

    private static bool IsTimedMedia(TimelineLaneDocument lane) =>
        string.Equals(lane.Type, "video", StringComparison.OrdinalIgnoreCase) ||
        string.Equals(lane.Type, "audio", StringComparison.OrdinalIgnoreCase);

    private static HashSet<string> GetPresentMediaProperties(JsonObject source)
    {
        var result = new HashSet<string>(StringComparer.Ordinal);
        foreach (var propertyName in MediaPropertyNames)
        {
            if (source.ContainsKey(propertyName) ||
                source["data"] is JsonObject data && data.ContainsKey(propertyName))
            {
                result.Add(propertyName);
            }
        }

        return result;
    }

    private static JsonNode? GetMediaNode(JsonObject source, string propertyName)
    {
        if (source.TryGetPropertyValue(propertyName, out var direct))
        {
            return direct;
        }

        return source["data"] is JsonObject data &&
               data.TryGetPropertyValue(propertyName, out var nested)
            ? nested
            : null;
    }

    private static string GetMediaString(
        JsonObject source,
        string propertyName,
        string fallback = "") =>
        GetString(GetMediaNode(source, propertyName)) ?? fallback;

    private static double GetMediaDouble(JsonObject source, string propertyName, double fallback) =>
        GetDouble(GetMediaNode(source, propertyName), fallback);

    private static bool GetMediaBoolean(JsonObject source, string propertyName, bool fallback)
    {
        var node = GetMediaNode(source, propertyName);
        return node is JsonValue value && value.TryGetValue<bool>(out var result) ? result : fallback;
    }

    private static void WriteMediaProperties(JsonObject source, TimelineLaneDocument lane)
    {
        foreach (var propertyName in lane.PresentMediaProperties)
        {
            JsonNode? value = propertyName switch
            {
                "source_path" => JsonValue.Create(lane.SourcePath),
                "source_in_s" => JsonValue.Create(Math.Max(0, lane.SourceInSeconds)),
                "source_out_s" => JsonValue.Create(Math.Max(0, lane.SourceOutSeconds)),
                "speed" => JsonValue.Create(Math.Clamp(lane.Speed, 0.25, 4)),
                "volume" => JsonValue.Create(Math.Clamp(lane.Volume, 0, 2)),
                "muted" => JsonValue.Create(lane.Muted),
                "fade_in_s" => JsonValue.Create(Math.Max(0, lane.FadeInSeconds)),
                "fade_out_s" => JsonValue.Create(Math.Max(0, lane.FadeOutSeconds)),
                "fit_mode" => JsonValue.Create(lane.FitMode),
                "opacity" => JsonValue.Create(Math.Clamp(lane.Opacity, 0, 1)),
                "brightness" => JsonValue.Create(Math.Clamp(lane.Brightness, -1, 1)),
                "contrast" => JsonValue.Create(Math.Clamp(lane.Contrast, 0, 2)),
                "saturation" => JsonValue.Create(Math.Clamp(lane.Saturation, 0, 3)),
                "rotation_deg" => JsonValue.Create(lane.RotationDegrees),
                "flip_horizontal" => JsonValue.Create(lane.FlipHorizontal),
                _ => null
            };
            if (value is null)
            {
                continue;
            }

            var wroteValue = false;
            if (source.ContainsKey(propertyName))
            {
                source[propertyName] = value.DeepClone();
                wroteValue = true;
            }

            if (source["data"] is JsonObject existingData && existingData.ContainsKey(propertyName))
            {
                existingData[propertyName] = value.DeepClone();
                wroteValue = true;
            }

            if (!wroteValue)
            {
                var data = source["data"] as JsonObject ?? [];
                data[propertyName] = value;
                source["data"] = data;
            }
        }
    }

    private static string NormalizeName(string? value, string fallback) =>
        string.IsNullOrWhiteSpace(value) ? fallback : value.Trim();

    private static string NormalizeType(string? value) =>
        string.IsNullOrWhiteSpace(value) ? "video" : value.Trim().ToLowerInvariant();

    private static string? GetString(JsonNode? node) =>
        node is JsonValue value && value.TryGetValue<string>(out var result) ? result : null;

    private static double GetDouble(JsonNode? node, double fallback)
    {
        if (node is not JsonValue value)
        {
            return fallback;
        }

        if (value.TryGetValue<double>(out var number) && double.IsFinite(number))
        {
            return number;
        }

        return value.TryGetValue<int>(out var integer) ? integer : fallback;
    }

    private static void ValidateTimelineDuration(double timelineDurationSeconds)
    {
        if (!double.IsFinite(timelineDurationSeconds) ||
            timelineDurationSeconds < MinimumDurationSeconds)
        {
            throw new ArgumentOutOfRangeException(
                nameof(timelineDurationSeconds),
                "Timeline duration must be at least 0.05 seconds.");
        }
    }

    private static void ValidateTimes(double startSeconds, double endSeconds)
    {
        if (!double.IsFinite(startSeconds) || startSeconds < 0)
        {
            throw new ArgumentOutOfRangeException(
                nameof(startSeconds),
                "Timeline start must be a finite non-negative value.");
        }

        if (!double.IsFinite(endSeconds) || endSeconds <= startSeconds)
        {
            throw new ArgumentOutOfRangeException(
                nameof(endSeconds),
                "Timeline end must be finite and greater than its start.");
        }
    }
}
