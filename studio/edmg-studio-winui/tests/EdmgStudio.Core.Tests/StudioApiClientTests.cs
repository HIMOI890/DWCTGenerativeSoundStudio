using System.Net;
using System.Text;
using System.Text.Json;
using EdmgStudio.Core.Models;
using EdmgStudio.Core.Services;

namespace EdmgStudio.Core.Tests;

[TestClass]
public sealed class StudioApiClientTests
{
    [TestMethod]
    public async Task ProjectWorkflow_UsesTheExactStudioHttpContract()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            var body = request.Content is null
                ? string.Empty
                : await request.Content.ReadAsStringAsync(cancellationToken);
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                body));

            return (request.Method.Method, request.RequestUri!.AbsolutePath) switch
            {
                ("GET", "/health") => JsonResponse("""{"ok":true,"version":"1.2.0"}"""),
                ("GET", "/v1/projects") => JsonResponse(ProjectListJson),
                ("POST", "/v1/projects") => JsonResponse(ProjectResponseJson),
                ("GET", "/v1/projects/p1") => JsonResponse(ProjectResponseJson),
                ("POST", "/v1/projects/p1/assets/audio") => JsonResponse("""{"ok":true}"""),
                ("POST", "/v1/projects/p1/analyze_audio") => JsonResponse("""{"ok":true,"analysis":{"features":{"bpm":128}}}"""),
                ("POST", "/v1/projects/p1/plan") => JsonResponse("""{"source":"local","duration_s":30,"variants":[{"index":0,"scenes":[]}]}"""),
                _ => new HttpResponseMessage(HttpStatusCode.NotFound)
            };
        }));
        var endpoint = new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/"));
        using var client = new StudioApiClient(endpoint, new StaticTokenProvider("test-token"), httpClient);

        var health = await client.GetHealthAsync();
        var projects = await client.GetProjectsAsync();
        var created = await client.CreateProjectAsync("  Native Project  ");
        var project = await client.GetProjectAsync("p1");
        await using var audio = new MemoryStream(Encoding.UTF8.GetBytes("audio-payload"));
        await client.UploadAudioAsync("p1", audio, "track.wav", "audio/wav");
        var analysis = await client.AnalyzeAudioAsync("p1");
        var plan = await client.GeneratePlanAsync(
            "p1",
            new PlanRequest("Native Project", "Keep the drop", "cinematic", 2, 8),
            "local");

        Assert.IsTrue(health.Ok);
        Assert.AreEqual("p1", projects.Projects.Single().Id);
        Assert.AreEqual("p1", created.Project.Id);
        Assert.AreEqual("p1", project.Project.Id);
        Assert.IsTrue(analysis.Ok);
        Assert.AreEqual("local", plan.Source);

        Assert.IsNull(captured.Single(item => item.Uri.AbsolutePath == "/health").Authorization);
        Assert.IsTrue(captured.Where(item => item.Uri.AbsolutePath != "/health")
            .All(item => item.Authorization == "Bearer test-token"));

        var createRequest = captured.Single(item => item.Method == HttpMethod.Post && item.Uri.AbsolutePath == "/v1/projects");
        using (var createJson = JsonDocument.Parse(createRequest.Body))
        {
            Assert.AreEqual("Native Project", createJson.RootElement.GetProperty("name").GetString());
        }

        var uploadRequest = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/assets/audio", StringComparison.Ordinal));
        Assert.AreEqual("multipart/form-data", uploadRequest.ContentType);
        StringAssert.Contains(uploadRequest.Body, "name=file");
        StringAssert.Contains(uploadRequest.Body, "filename=track.wav");
        StringAssert.Contains(uploadRequest.Body, "audio-payload");

        var analyzeRequest = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/analyze_audio", StringComparison.Ordinal));
        Assert.AreEqual("{}", analyzeRequest.Body);

        var planRequest = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/plan", StringComparison.Ordinal));
        Assert.AreEqual("?mode=local", planRequest.Uri.Query);
        using (var planJson = JsonDocument.Parse(planRequest.Body))
        {
            Assert.AreEqual(2, planJson.RootElement.GetProperty("num_variants").GetInt32());
            Assert.AreEqual(8, planJson.RootElement.GetProperty("max_scenes").GetInt32());
            Assert.AreEqual("cinematic", planJson.RootElement.GetProperty("style_prefs").GetString());
        }
    }

    [TestMethod]
    public async Task ErrorEnvelope_BecomesAnActionableStudioException()
    {
        using var httpClient = new HttpClient(new RecordingHandler((_, _) => Task.FromResult(
            JsonResponse(
                """{"error":{"code":"AUDIO_REQUIRED","message":"Audio is required.","hint":"Upload a track first."}}""",
                HttpStatusCode.UnprocessableEntity))));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        var exception = await Assert.ThrowsExactlyAsync<StudioApiException>(() => client.GetProjectsAsync());

        Assert.AreEqual(HttpStatusCode.UnprocessableEntity, exception.StatusCode);
        Assert.AreEqual("AUDIO_REQUIRED", exception.Code);
        Assert.AreEqual("Audio is required. Upload a track first.", exception.UserFacingMessage);
    }

    [TestMethod]
    public async Task StableProjectRevisionConflict_BecomesTypedExceptionWithMetadata()
    {
        using var httpClient = new HttpClient(new RecordingHandler((_, _) => Task.FromResult(
            JsonResponse(
                """
                {
                  "error": {
                    "code": "project_revision_conflict",
                    "message": "The project changed.",
                    "hint": "Reload before retrying.",
                    "project_id": "project-7",
                    "details": {
                      "expected_revision": "12",
                      "current_revision": 14
                    }
                  }
                }
                """,
                HttpStatusCode.Conflict))));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        var exception = await Assert.ThrowsExactlyAsync<ProjectRevisionConflictException>(
            () => client.GetProjectsAsync());

        Assert.AreEqual(HttpStatusCode.Conflict, exception.StatusCode);
        Assert.AreEqual(ProjectRevisionConflictException.ErrorCode, exception.Code);
        Assert.AreEqual("project-7", exception.ProjectId);
        Assert.AreEqual(12L, exception.ExpectedRevision);
        Assert.AreEqual(14L, exception.ActualRevision);
        Assert.AreEqual("The project changed. Reload before retrying.", exception.UserFacingMessage);
    }

    [TestMethod]
    public async Task UnrelatedConflict_RemainsExactlyStudioApiException()
    {
        using var httpClient = new HttpClient(new RecordingHandler((_, _) => Task.FromResult(
            JsonResponse(
                """{"error":{"code":"PROJECT_NAME_CONFLICT","message":"That name is already used.","hint":"Choose another name."}}""",
                HttpStatusCode.Conflict))));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        var exception = await Assert.ThrowsExactlyAsync<StudioApiException>(
            () => client.GetProjectsAsync());

        Assert.AreEqual(HttpStatusCode.Conflict, exception.StatusCode);
        Assert.AreEqual("PROJECT_NAME_CONFLICT", exception.Code);
    }

    [TestMethod]
    public async Task RevisionAwareMutations_SerializeExpectedRevisionAndLegacyCallsOmitIt()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null
                    ? string.Empty
                    : await request.Content.ReadAsStringAsync(cancellationToken)));

            return request.RequestUri!.AbsolutePath switch
            {
                var path when path.EndsWith("/plan", StringComparison.Ordinal) =>
                    JsonResponse("""{"source":"local","duration_s":30,"variants":[]}"""),
                var path when path.EndsWith("/template_package/import", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"applied":[]}"""),
                var path when path.EndsWith("/timeline/apply_plan", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"timeline":{},"variant_index":0}"""),
                var path when path.EndsWith("/plan/variant", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"variant_index":0}"""),
                var path when path.EndsWith("/planner_lab/import", StringComparison.Ordinal) =>
                    JsonResponse(
                        """{"ok":true,"plan":{"source":"planner_lab","variants":[]},"timeline":{},"visual_dna":{},"visual_dna_hints":{}}"""),
                var path when path.EndsWith("/reactive_lab/apply", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"timeline":{},"visual_dna":{},"visual_dna_hints":{}}"""),
                var path when path.EndsWith("/motion_grammar/apply", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"timeline":{}}"""),
                _ => JsonResponse("""{"ok":true}"""),
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("revision-token"),
            httpClient);
        using var objectDocument = JsonDocument.Parse("""{"value":1}""");
        JsonElement payload = objectDocument.RootElement.Clone();

        await client.GeneratePlanAsync(
            "revisioned",
            new PlanRequest("Project", "brief", "style", 1, 8, ExpectedRevision: 11),
            "local");
        await client.ImportProjectTemplatePackageAsync(
            "revisioned",
            new TemplatePackageDto { SchemaVersion = 1, Payload = payload },
            merge: true,
            expectedRevision: 12);
        await client.ApplyPlanToTimelineAsync(
            "revisioned",
            variantIndex: 0,
            overwrite: true,
            expectedRevision: 13);
        await client.UpdatePlanVariantAsync(
            "revisioned",
            variantIndex: 0,
            scenes: [],
            expectedRevision: 14);
        await client.ImportPlannerLabAsync(
            "revisioned",
            new PlannerLabImportRequest
            {
                Analysis = payload,
                Plan = payload,
                Settings = new PlannerLabSettings(),
                ExpectedRevision = 15,
            });
        await client.ApplyReactiveLabAsync(
            "revisioned",
            new ReactiveLabApplyRequest
            {
                Schedules = payload,
                ExpectedRevision = 16,
            });
        await client.SaveTimelineAsync("revisioned", payload, expectedRevision: 17);
        await client.AutosaveTimelineAsync(
            "revisioned",
            payload,
            payload,
            "interval",
            expectedRevision: 18);
        await client.ApplyMotionGrammarAsync(
            "revisioned",
            [new MotionPhraseRequest("settle", 0, 2)],
            overwriteMotionTrack: false,
            expectedRevision: 19);
        await client.ApplyRecoveryAsync(
            "revisioned",
            new RecoveryApplyRequest(ExpectedRevision: 20));
        await client.SaveTimelineAsync("legacy", payload);

        AssertExpectedRevision("/v1/projects/revisioned/plan", 11);
        AssertExpectedRevision("/v1/projects/revisioned/template_package/import", 12);
        AssertExpectedRevision("/v1/projects/revisioned/timeline/apply_plan", 13);
        AssertExpectedRevision("/v1/projects/revisioned/plan/variant", 14);
        AssertExpectedRevision("/v1/projects/revisioned/planner_lab/import", 15);
        AssertExpectedRevision("/v1/projects/revisioned/reactive_lab/apply", 16);
        AssertExpectedRevision("/v1/projects/revisioned/timeline", 17);
        AssertExpectedRevision("/v1/projects/revisioned/autosave", 18);
        AssertExpectedRevision("/v1/projects/revisioned/motion_grammar/apply", 19);
        AssertExpectedRevision("/v1/projects/revisioned/recovery/apply", 20);

        using var legacyPayload = JsonDocument.Parse(
            captured.Single(item => item.Uri.AbsolutePath == "/v1/projects/legacy/timeline").Body);
        Assert.IsFalse(legacyPayload.RootElement.TryGetProperty("expected_revision", out _));

        void AssertExpectedRevision(string path, long expectedRevision)
        {
            using var document = JsonDocument.Parse(
                captured.Single(item => item.Uri.AbsolutePath == path).Body);
            Assert.AreEqual(
                expectedRevision,
                document.RootElement.GetProperty("expected_revision").GetInt64(),
                path);
        }
    }

    [TestMethod]
    public async Task PlannerReactiveTypedMethods_UseExactContractsAndDeserializeReadiness()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null
                    ? string.Empty
                    : await request.Content.ReadAsStringAsync(cancellationToken)));

            return request.RequestUri!.AbsolutePath switch
            {
                "/v1/ai/status" => JsonResponse(
                    """{"ok":true,"ai":{"available":true},"ai_config":{"mode":"provider","provider":"openai","label":"OpenAI","model":"gpt-4.1","ready":true,"future_field":"kept"}}"""),
                "/v1/projects/p%20%2F%231/plan" => JsonResponse(
                    """{"source":"edmg_core","duration_s":30,"variants":[{"index":0,"scenes":[]}]}"""),
                "/v1/projects/p%20%2F%231/planner_lab/import" => JsonResponse(
                    """{"ok":true,"plan":{"source":"planner_lab","duration_s":30,"variants":[]},"timeline":{},"visual_dna":{},"visual_dna_hints":{}}"""),
                "/v1/projects/p%20%2F%231/reactive_lab/apply" => JsonResponse(
                    """{"ok":true,"timeline":{"duration_s":30},"visual_dna":{},"visual_dna_hints":{}}"""),
                _ => new HttpResponseMessage(HttpStatusCode.NotFound),
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("typed-token"),
            httpClient);
        using var objectDocument = JsonDocument.Parse("""{"value":1}""");
        using var scheduleDocument = JsonDocument.Parse("""{"zoom":"0:(1.0)"}""");

        var readiness = await client.GetAiReadinessAsync();
        var plan = await client.GeneratePlanAsync(
            "p /#1",
            new PlanRequest("Project", "brief", "style", 1, 8),
            "edmg_core");
        var imported = await client.ImportPlannerLabAsync(
            "p /#1",
            new PlannerLabImportRequest
            {
                Analysis = objectDocument.RootElement.Clone(),
                Plan = objectDocument.RootElement.Clone(),
                Settings = new PlannerLabSettings { SceneCount = 6 },
                ApplyTimeline = false,
                OverwriteTimeline = false,
            });
        var applied = await client.ApplyReactiveLabAsync(
            "p /#1",
            new ReactiveLabApplyRequest
            {
                Metadata = objectDocument.RootElement.Clone(),
                Schedules = scheduleDocument.RootElement.Clone(),
                OverwriteMotionTrack = false,
                OverwriteCamera = true,
            });

        Assert.AreEqual("OpenAI", readiness.AiConfiguration.Label);
        Assert.AreEqual("gpt-4.1", readiness.AiConfiguration.Model);
        Assert.AreEqual("kept", readiness.AiConfiguration.AdditionalData!["future_field"].GetString());
        Assert.AreEqual("edmg_core", plan.Source);
        Assert.IsTrue(imported.Ok);
        Assert.IsTrue(applied.Ok);

        var planRequest = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/plan", StringComparison.Ordinal));
        Assert.AreEqual("?mode=edmg_core", planRequest.Uri.Query);

        using var plannerJson = JsonDocument.Parse(
            captured.Single(item => item.Uri.AbsolutePath.EndsWith("/planner_lab/import", StringComparison.Ordinal)).Body);
        Assert.AreEqual(6, plannerJson.RootElement.GetProperty("settings").GetProperty("sceneCount").GetInt32());
        Assert.IsFalse(plannerJson.RootElement.GetProperty("apply_timeline").GetBoolean());
        Assert.IsFalse(plannerJson.RootElement.GetProperty("overwrite_timeline").GetBoolean());

        using var reactiveJson = JsonDocument.Parse(
            captured.Single(item => item.Uri.AbsolutePath.EndsWith("/reactive_lab/apply", StringComparison.Ordinal)).Body);
        Assert.AreEqual("0:(1.0)", reactiveJson.RootElement.GetProperty("schedules").GetProperty("zoom").GetString());
        Assert.IsFalse(reactiveJson.RootElement.GetProperty("overwrite_motion_track").GetBoolean());
        Assert.IsTrue(reactiveJson.RootElement.GetProperty("overwrite_camera").GetBoolean());
    }

    [TestMethod]
    public async Task ReactiveTypedMethod_RejectsEmptyPayloadBeforeNetworkUse()
    {
        var callCount = 0;
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            callCount++;
            return Task.FromResult(JsonResponse("{}"));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.ApplyReactiveLabAsync("p1", new ReactiveLabApplyRequest()));
        Assert.AreEqual(0, callCount);
    }

    [TestMethod]
    public async Task InvalidProjectAndPlanInputs_AreRejectedBeforeNetworkUse()
    {
        var callCount = 0;
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            callCount++;
            return Task.FromResult(JsonResponse("{}"));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        await Assert.ThrowsExactlyAsync<ArgumentException>(() => client.CreateProjectAsync("   "));
        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() => client.GeneratePlanAsync(
            "p1",
            new PlanRequest(null, null, null, 11, 12)));
        Assert.AreEqual(0, callCount);
    }

    [TestMethod]
    public async Task TimelineRecoveryAndSecrets_UseExactBackendRequestBodies()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            var body = request.Content is null
                ? string.Empty
                : await request.Content.ReadAsStringAsync(cancellationToken);
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                body));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);
        using var timeline = JsonDocument.Parse("""{"layers":[{"id":"layer-1"}]}""");
        using var metadata = JsonDocument.Parse("""{"playhead":12.5}""");

        await client.AutosaveTimelineAsync(
            "project one",
            timeline.RootElement.Clone(),
            metadata.RootElement.Clone(),
            "interval");
        await client.ApplyRecoveryAsync(
            "project one",
            new RecoveryApplyRequest("snapshot", "snapshot-2026-08-12"));
        await client.SetSecretAsync("foundry_api_key", "secret-value");
        await client.ClearSecretAsync("foundry_api_key");

        var autosave = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/autosave", StringComparison.Ordinal));
        Assert.AreEqual("/v1/projects/project%20one/autosave", autosave.Uri.AbsolutePath);
        Assert.IsNotNull(autosave.Authorization);
        using (var payload = JsonDocument.Parse(autosave.Body))
        {
            Assert.IsTrue(payload.RootElement.TryGetProperty("meta", out var serializedMetadata));
            Assert.AreEqual(12.5, serializedMetadata.GetProperty("playhead").GetDouble());
            Assert.IsFalse(payload.RootElement.TryGetProperty("metadata", out _));
            Assert.AreEqual(
                "layer-1",
                payload.RootElement.GetProperty("timeline").GetProperty("layers")[0].GetProperty("id").GetString());
        }

        var recovery = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/recovery/apply", StringComparison.Ordinal));
        using (var payload = JsonDocument.Parse(recovery.Body))
        {
            Assert.AreEqual("snapshot", payload.RootElement.GetProperty("source").GetString());
            Assert.AreEqual("snapshot-2026-08-12", payload.RootElement.GetProperty("snapshot_name").GetString());
            Assert.IsFalse(payload.RootElement.TryGetProperty("prefer_journal", out _));
        }

        var setSecret = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/secrets/set", StringComparison.Ordinal));
        using (var payload = JsonDocument.Parse(setSecret.Body))
        {
            Assert.AreEqual("foundry_api_key", payload.RootElement.GetProperty("name").GetString());
            Assert.AreEqual("secret-value", payload.RootElement.GetProperty("value").GetString());
            Assert.IsFalse(payload.RootElement.TryGetProperty("key", out _));
        }

        var clearSecret = captured.Single(item => item.Uri.AbsolutePath.EndsWith("/secrets/clear", StringComparison.Ordinal));
        using (var payload = JsonDocument.Parse(clearSecret.Body))
        {
            Assert.AreEqual("foundry_api_key", payload.RootElement.GetProperty("name").GetString());
            Assert.IsFalse(payload.RootElement.TryGetProperty("key", out _));
        }
    }

    [TestMethod]
    public async Task QueueTimelineRenderAsync_UsesTypedAuthenticatedContract()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                await request.Content!.ReadAsStringAsync(cancellationToken));
            return JsonResponse(
                """
                {
                  "ok": true,
                  "job": {
                    "id": "timeline-job",
                    "project_id": "project one",
                    "type": "timeline_render",
                    "status": "queued"
                  }
                }
                """);
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        var response = await client.QueueTimelineRenderAsync(
            "project one",
            new TimelineRenderRequest(1920, 1080, 24, "H264", "AAC", 18, "edited-master"));

        Assert.IsTrue(response.Ok);
        Assert.AreEqual("timeline-job", response.Job.Id);
        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Post, captured.Method);
        Assert.AreEqual("/v1/projects/project%20one/timeline/render", captured.Uri.AbsolutePath);
        Assert.AreEqual("Bearer session-token", captured.Authorization);
        Assert.AreEqual("application/json", captured.ContentType);
        using var payload = JsonDocument.Parse(captured.Body);
        Assert.AreEqual(1920, payload.RootElement.GetProperty("width").GetInt32());
        Assert.AreEqual(1080, payload.RootElement.GetProperty("height").GetInt32());
        Assert.AreEqual(24, payload.RootElement.GetProperty("fps").GetDouble());
        Assert.AreEqual("h264", payload.RootElement.GetProperty("video_codec").GetString());
        Assert.AreEqual("aac", payload.RootElement.GetProperty("audio_codec").GetString());
        Assert.AreEqual(18, payload.RootElement.GetProperty("quality").GetInt32());
        Assert.AreEqual("edited-master", payload.RootElement.GetProperty("name").GetString());
    }

    [TestMethod]
    public async Task InvalidTimelineRenderRequests_AreRejectedBeforeNetworkUse()
    {
        var callCount = 0;
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            callCount++;
            return Task.FromResult(JsonResponse("{}"));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() => client.QueueTimelineRenderAsync(
            "p1",
            new TimelineRenderRequest(1919, 1080, 24, "h264", "aac", 23, "master")));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() => client.QueueTimelineRenderAsync(
            "p1",
            new TimelineRenderRequest(1920, 1080, 24, "prores", "aac", 23, "master")));
        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() => client.QueueTimelineRenderAsync(
            "p1",
            new TimelineRenderRequest(1920, 1080, 24, "h264", "aac", 0, "master")));
        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() => client.QueueTimelineRenderAsync(
            "p1",
            new TimelineRenderRequest(1920, 1080, 24, "h264", "aac", 52, "master")));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() => client.QueueTimelineRenderAsync(
            "p1",
            new TimelineRenderRequest(1920, 1080, 24, "h264", "aac", 23, "../master")));

        Assert.AreEqual(0, callCount);
    }

    [TestMethod]
    public async Task ApplyMotionGrammarAsync_UsesTypedAuthenticatedContract()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                await request.Content!.ReadAsStringAsync(cancellationToken));
            return JsonResponse(
                """
                {
                  "ok": true,
                  "timeline": {
                    "tracks": [{"name": "Motion"}]
                  }
                }
                """);
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        var response = await client.ApplyMotionGrammarAsync(
            "project / one",
            [
                new MotionPhraseRequest("prepare", 0, 4),
                new MotionPhraseRequest(
                    "accent",
                    4,
                    6,
                    new Dictionary<string, double> { ["intensity"] = 0.8 }),
            ],
            true);

        Assert.IsTrue(response.Ok);
        Assert.AreEqual("Motion", response.Timeline.GetProperty("tracks")[0].GetProperty("name").GetString());
        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Post, captured.Method);
        Assert.AreEqual("/v1/projects/project%20%2F%20one/motion_grammar/apply", captured.Uri.AbsolutePath);
        Assert.AreEqual("Bearer " + "session" + "-token", captured.Authorization);
        Assert.AreEqual("application/json", captured.ContentType);

        using var payload = JsonDocument.Parse(captured.Body);
        Assert.IsTrue(payload.RootElement.GetProperty("overwrite_motion_track").GetBoolean());
        var phrases = payload.RootElement.GetProperty("phrases");
        Assert.AreEqual(2, phrases.GetArrayLength());
        Assert.AreEqual("prepare", phrases[0].GetProperty("phrase").GetString());
        Assert.AreEqual(0D, phrases[0].GetProperty("start_s").GetDouble());
        Assert.AreEqual(4D, phrases[0].GetProperty("end_s").GetDouble());
        Assert.IsFalse(phrases[0].TryGetProperty("overrides", out _));
        Assert.AreEqual(0.8D, phrases[1].GetProperty("overrides").GetProperty("intensity").GetDouble());
    }

    [TestMethod]
    public async Task ApplyMotionGrammarAsync_RejectsEmptyPhrasesBeforeNetworkUse()
    {
        var callCount = 0;
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            callCount++;
            return Task.FromResult(JsonResponse("{}"));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.ApplyMotionGrammarAsync("p1", Array.Empty<MotionPhraseRequest>(), false));

        Assert.AreEqual(0, callCount);
    }

    [TestMethod]
    public async Task ApplyMotionGrammarAsync_PropagatesCancellationToTransport()
    {
        var handlerStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        CancellationToken observedToken = default;
        using var httpClient = new HttpClient(new RecordingHandler(async (_, cancellationToken) =>
        {
            observedToken = cancellationToken;
            handlerStarted.SetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return JsonResponse("""{"ok":true,"timeline":{}}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);
        using var cancellation = new CancellationTokenSource();

        var request = client.ApplyMotionGrammarAsync(
            "p1",
            [new MotionPhraseRequest("settle", 0, 2)],
            false,
            cancellation.Token);
        await handlerStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() => request);
        Assert.IsTrue(observedToken.CanBeCanceled);
        Assert.IsTrue(observedToken.IsCancellationRequested);
    }

    [TestMethod]
    public async Task GetJobsAsync_PreservesProgressAndExposesValidActions()
    {
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            Assert.AreEqual("/v1/jobs", request.RequestUri!.AbsolutePath);
            return Task.FromResult(JsonResponse(
                """
                {
                  "jobs": [{
                    "id": "job-42",
                    "project_id": "project-alpha",
                    "type": "internal_video",
                    "status": "running",
                    "created_at": "2026-08-12T08:00:00Z",
                    "progress": {
                      "percent": 37.5,
                      "stage": "render",
                      "message": "Rendering frames",
                      "current": 45,
                      "total": 120
                    }
                  }]
                }
                """));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        var result = await client.GetJobsAsync();

        var job = result.Jobs.Single();
        Assert.AreEqual("job-42", job.Id);
        Assert.IsTrue(job.IsActive);
        Assert.IsTrue(job.CanCancel);
        Assert.IsFalse(job.CanRetry);
        Assert.AreEqual(37.5, job.Progress?.Percent);
    }

    [TestMethod]
    public async Task QueueRecoveryActions_UseExactProjectJobRoutes()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null
                    ? string.Empty
                    : await request.Content.ReadAsStringAsync(cancellationToken)));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        await client.ResumeJobFromCheckpointAsync("project one", "job one");
        await client.RestartJobCleanAsync("project one", "job one");
        await client.ClearJobCachedFramesAsync("project one", "job one");
        await client.DropJobCheckpointAsync("project one", "job one");

        var expectedPaths = new[]
        {
            "/v1/projects/project%20one/jobs/job%20one/resume_from_checkpoint",
            "/v1/projects/project%20one/jobs/job%20one/restart_clean",
            "/v1/projects/project%20one/jobs/job%20one/clear_cached_frames",
            "/v1/projects/project%20one/jobs/job%20one/drop_checkpoint",
        };
        CollectionAssert.AreEqual(expectedPaths, captured.Select(item => item.Uri.AbsolutePath).ToArray());
        Assert.IsTrue(captured.All(item => item.Method == HttpMethod.Post));
        Assert.IsTrue(captured.All(item => item.Authorization == "Bearer session-token"));
        Assert.IsTrue(captured.All(item => item.ContentType == "application/json"));
        Assert.IsTrue(captured.All(item => item.Body == "{}"));
    }

    [TestMethod]
    public async Task DownloadProjectFileAsync_UsesAuthenticationEscapingAndPreservesBytes()
    {
        byte[] expected = [0x00, 0x7F, 0x80, 0xFF];
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, _) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync());
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(expected)
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        var actual = await client.DownloadProjectFileAsync("project /#1", "renders/final take #1.mp4");

        CollectionAssert.AreEqual(expected, actual);
        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Get, captured.Method);
        Assert.AreEqual("/v1/projects/project%20%2F%231/file", captured.Uri.AbsolutePath);
        Assert.AreEqual("?path=renders%2Ffinal%20take%20%231.mp4", captured.Uri.Query);
        Assert.IsNotNull(captured.Authorization);
    }

    [TestMethod]
    public async Task StreamProjectFileAsync_KeepsResponseAliveForAuthenticatedCallbackThenDisposesIt()
    {
        byte[] expected = [0x01, 0x02, 0xFE, 0xFF];
        var content = new TrackingContent(expected);
        string? authorization = null;
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            authorization = request.Headers.Authorization?.ToString();
            var response = new HttpResponseMessage(HttpStatusCode.OK) { Content = content };
            response.Headers.TryAddWithoutValidation("X-Preview-Source", "stream");
            return Task.FromResult(response);
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            httpClient);

        var actual = await client.StreamProjectFileAsync(
            "p1",
            "renders/preview.png",
            async (file, cancellationToken) =>
            {
                Assert.IsFalse(content.IsDisposed);
                Assert.AreEqual(HttpStatusCode.OK, file.StatusCode);
                Assert.AreEqual(expected.Length, file.ContentHeaders.ContentLength);
                Assert.AreEqual("stream", file.ResponseHeaders.GetValues("X-Preview-Source").Single());

                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                Assert.IsFalse(content.IsDisposed);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.AreEqual("Bearer stream-token", authorization);
        Assert.IsTrue(content.IsDisposed);
        Assert.IsTrue(content.Stream.IsDisposed);
    }

    [TestMethod]
    public async Task StreamProjectFileAsync_CancellationDisposesTheResponseLifetime()
    {
        var content = new TrackingContent([0x01]);
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content })));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            httpClient);
        using var cancellation = new CancellationTokenSource();

        var streaming = client.StreamProjectFileAsync(
            "p1",
            "renders/preview.png",
            async (_, cancellationToken) =>
            {
                cancellation.Cancel();
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
                return false;
            },
            cancellation.Token);

        await Assert.ThrowsAsync<OperationCanceledException>(async () => await streaming);
        Assert.IsTrue(content.IsDisposed);
        Assert.IsTrue(content.Stream.IsDisposed);
    }

    [TestMethod]
    public async Task GetProjectMediaUrlsAsync_UsesAuthenticatedSignedMediaContract()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken));
            return JsonResponse(
                """
                {
                  "expires_at": 1760000000,
                  "urls": [
                    {
                      "purpose": "preview",
                      "url": "/signed/preview/frame?t=1.25&sig=test"
                    }
                  ]
                }
                """);
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("signed-token"),
            httpClient);

        SignedMediaUrlBatchResponse response = await client.GetProjectMediaUrlsAsync(
            "project /#1",
            [
                new SignedMediaUrlRequest
                {
                    Purpose = "preview",
                    Query = JsonSerializer.SerializeToElement(new { t = 1.25, variant_index = 2 })
                }
            ]);

        Assert.AreEqual(1760000000L, response.ExpiresAtUnixSeconds);
        Assert.AreEqual("/signed/preview/frame?t=1.25&sig=test", response.Urls.Single().Url);
        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Post, captured.Method);
        Assert.AreEqual("/v1/projects/project%20%2F%231/media-urls", captured.Uri.AbsolutePath);
        Assert.AreEqual("Bearer " + "signed" + "-token", captured.Authorization);
        Assert.AreEqual("application/json", captured.ContentType);
        using var payload = JsonDocument.Parse(captured.Body);
        JsonElement request = payload.RootElement.GetProperty("requests")[0];
        Assert.AreEqual("preview", request.GetProperty("purpose").GetString());
        Assert.AreEqual(1.25, request.GetProperty("query").GetProperty("t").GetDouble());
        Assert.AreEqual(2, request.GetProperty("query").GetProperty("variant_index").GetInt32());
        Assert.IsFalse(request.TryGetProperty("path", out _));
    }

    [TestMethod]
    public async Task StreamProjectPreviewFileAsync_UsesSignedMediaUrlWithoutBearerOnDownload()
    {
        byte[] expected = [0xAA, 0xBB, 0xCC];
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken)));

            return (request.Method.Method, request.RequestUri!.AbsolutePath) switch
            {
                ("POST", "/v1/projects/p1/media-urls") => JsonResponse(
                    """
                    {
                      "expires_at": 1760000000,
                      "urls": [
                        {
                          "purpose": "file",
                          "url": "/signed/media?sig=preview"
                        }
                      ]
                    }
                    """),
                ("GET", "/signed/media") => new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new ByteArrayContent(expected)
                },
                _ => new HttpResponseMessage(HttpStatusCode.NotFound)
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("preview-token"),
            httpClient);

        byte[] actual = await client.StreamProjectPreviewFileAsync(
            "p1",
            "renders/preview.png",
            async (file, cancellationToken) =>
            {
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.AreEqual(2, captured.Count);
        Assert.AreEqual("/v1/projects/p1/media-urls", captured[0].Uri.AbsolutePath);
        Assert.AreEqual("Bearer " + "preview" + "-token", captured[0].Authorization);
        Assert.AreEqual("/signed/media", captured[1].Uri.AbsolutePath);
        Assert.IsNull(captured[1].Authorization);
    }

    [TestMethod]
    public async Task StreamProjectPreviewFileAsync_FallsBackToLegacyFileRouteWhenSignedMediaIsUnavailable()
    {
        byte[] expected = [0xAA, 0xBB, 0xCC];
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken)));

            return (request.Method.Method, request.RequestUri!.AbsolutePath) switch
            {
                ("POST", "/v1/projects/p1/media-urls") => JsonResponse(
                    """{"error":{"code":"NOT_FOUND","message":"missing"}}""",
                    HttpStatusCode.NotFound),
                ("GET", "/v1/projects/p1/file") => new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new ByteArrayContent(expected)
                },
                _ => new HttpResponseMessage(HttpStatusCode.NotFound)
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("preview-token"),
            httpClient);

        byte[] actual = await client.StreamProjectPreviewFileAsync(
            "p1",
            "renders/preview.png",
            async (file, cancellationToken) =>
            {
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.AreEqual(2, captured.Count);
        Assert.AreEqual("/v1/projects/p1/media-urls", captured[0].Uri.AbsolutePath);
        Assert.AreEqual("/v1/projects/p1/file", captured[1].Uri.AbsolutePath);
        Assert.AreEqual("?path=renders%2Fpreview.png", captured[1].Uri.Query);
        Assert.AreEqual("Bearer " + "preview" + "-token", captured[1].Authorization);
    }

    [TestMethod]
    public async Task StreamTimelineFrameAsync_UsesAuthenticatedExactQueryAndCallbackLifetime()
    {
        byte[] expected = [0x89, 0x50, 0x4E, 0x47];
        var content = new TrackingContent(expected);
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                string.Empty);
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content });
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            httpClient);

        var actual = await client.StreamTimelineFrameAsync(
            "project /#1",
            1.25,
            768,
            432,
            force: false,
            async (file, cancellationToken) =>
            {
                Assert.IsFalse(content.IsDisposed);
                using var copy = new MemoryStream();
                await file.Stream.CopyToAsync(copy, cancellationToken);
                return copy.ToArray();
            });

        CollectionAssert.AreEqual(expected, actual);
        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Get, captured.Method);
        Assert.AreEqual("/v1/projects/project%20%2F%231/preview/frame", captured.Uri.AbsolutePath);
        Assert.AreEqual("?t=1.25&w=768&h=432&force=0", captured.Uri.Query);
        Assert.AreEqual("Bearer " + "stream" + "-token", captured.Authorization);
        Assert.IsTrue(content.IsDisposed);
        Assert.IsTrue(content.Stream.IsDisposed);
    }

    [TestMethod]
    public async Task StreamTimelineFrameAsync_CancellationDisposesTheResponseLifetime()
    {
        var content = new TrackingContent([0x01]);
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = content })));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("stream-token"),
            httpClient);
        using var cancellation = new CancellationTokenSource();

        var streaming = client.StreamTimelineFrameAsync(
            "p1",
            0,
            768,
            432,
            force: false,
            async (_, cancellationToken) =>
            {
                cancellation.Cancel();
                await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
                return false;
            },
            cancellation.Token);

        await Assert.ThrowsAsync<OperationCanceledException>(async () => await streaming);
        Assert.IsTrue(content.IsDisposed);
        Assert.IsTrue(content.Stream.IsDisposed);
    }

    [TestMethod]
    public void StreamTimelineFrameAsync_RejectsInvalidGeometryBeforeNetworkUse()
    {
        var callCount = 0;
        using var httpClient = new HttpClient(new RecordingHandler((_, _) =>
        {
            callCount++;
            return Task.FromResult(JsonResponse("{}"));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() =>
            client.StreamTimelineFrameAsync(
                "p1",
                double.NaN,
                768,
                432,
                false,
                (_, _) => Task.FromResult(true)));
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() =>
            client.StreamTimelineFrameAsync(
                "p1",
                0,
                0,
                432,
                false,
                (_, _) => Task.FromResult(true)));
        Assert.AreEqual(0, callCount);
    }

    [TestMethod]
    public async Task ModelActions_UseExactPathsAndBodies()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, _) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync()));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        await client.AcceptModelLicenseAsync("model /#1", "license /#1");
        await client.RestoreLocalModelAsync("local /#2");

        Assert.HasCount(2, captured);
        Assert.AreEqual("/v1/models/accept", captured[0].Uri.AbsolutePath);
        Assert.AreEqual("""{"model_id":"model /#1","license_id":"license /#1"}""", captured[0].Body);
        Assert.AreEqual("/v1/models/restore_local", captured[1].Uri.AbsolutePath);
        Assert.AreEqual("""{"model_id":"local /#2"}""", captured[1].Body);
    }

    [TestMethod]
    public async Task ModelTasks_DeserializeProgressAndPresentationState()
    {
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            Assert.AreEqual(HttpMethod.Get, request.Method);
            Assert.AreEqual("/v1/models/tasks", request.RequestUri!.AbsolutePath);
            return Task.FromResult(JsonResponse(
                """
                {"tasks":[{"id":"task-1","name":"Install model","status":"running","progress":1.4,"last_log":"Downloading","error":null,"started_at":1.0,"ended_at":null,"model_id":"sd15","stage":null,"bytes_completed":1024,"bytes_total":2048,"files_completed":1,"files_total":2,"cancel_requested":false}]}
                """));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        var response = await client.GetModelTasksAsync();
        IReadOnlyList<ModelTask> tasks = response.Tasks!;
        var task = tasks.Single();

        Assert.IsTrue(task.IsActive);
        Assert.AreEqual(1d, task.ClampedProgress);
        Assert.AreEqual("Downloading", task.DisplayStage);
        Assert.AreEqual("task-1:running", ModelTask.Fingerprint(tasks));
    }

    [TestMethod]
    public async Task ModelCatalogue_UsesGeneratedMetadataAndPreservesUnknownFields()
    {
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            Assert.AreEqual(HttpMethod.Get, request.Method);
            Assert.AreEqual("/v1/models/catalog", request.RequestUri!.AbsolutePath);
            return Task.FromResult(JsonResponse(
                """
                {
                  "catalog": [{
                    "id": "sd15",
                    "name": "Stable Diffusion 1.5",
                    "kind": "checkpoint",
                    "installed": true,
                    "available": true,
                    "future_compatibility": "kept"
                  }],
                  "user": [],
                  "packs": [{
                    "id": "starter",
                    "name": "Starter pack",
                    "models": ["sd15"],
                    "future_pack_field": 7
                  }],
                  "accepted": {},
                  "installed": {},
                  "cloud": {},
                  "lanes": {},
                  "storage_mode": "local",
                  "model_cache": "cache/models",
                  "future_catalogue_field": {"enabled": true}
                }
                """));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);

        var response = await client.GetTypedModelCatalogueAsync();
        var model = response.Catalog!.Single();
        var pack = response.Packs!.Single();

        Assert.AreEqual("sd15", model.Id);
        Assert.IsTrue(model.Installed);
        Assert.AreEqual("kept", model.ExtensionData!["future_compatibility"].GetString());
        Assert.AreEqual(7, pack.ExtensionData!["future_pack_field"].GetInt32());
        Assert.IsTrue(response.ExtensionData!["future_catalogue_field"].GetProperty("enabled").GetBoolean());
    }

    [TestMethod]
    public async Task ModelManagement_UsesExactPathsBodiesAndTypedResponses()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken)));

            return request.RequestUri!.AbsolutePath switch
            {
                "/v1/models/benchmark" => JsonResponse("""{"ok":true,"benchmark":{"passed":true}}"""),
                "/v1/models/import/civitai" => JsonResponse("""{"entry":{"id":"civitai-model"}}"""),
                "/v1/models/import/local" => JsonResponse("""{"entry":{"id":"local-model"}}"""),
                "/v1/models/tensorrt/cancel-import" => JsonResponse(ModelTaskActionJson),
                "/v1/models/tensorrt/import-legacy" => JsonResponse(ModelTaskActionJson),
                _ => throw new InvalidOperationException(request.RequestUri.AbsolutePath)
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        var benchmark = await client.RecordModelBenchmarkAsync("model-1");
        var civitai = await client.ImportCivitaiModelAsync("https://civitai.com/models/1");
        var local = await client.ImportLocalModelAsync(@"C:\models\model.safetensors", "checkpoints");
        var migration = await client.ImportLegacyTensorRtAsync();
        var cancellation = await client.CancelLegacyTensorRtImportAsync("task-1");

        Assert.IsTrue(benchmark.Ok);
        Assert.AreEqual("civitai-model", civitai.Entry.GetProperty("id").GetString());
        Assert.AreEqual("local-model", local.Entry.GetProperty("id").GetString());
        Assert.AreEqual("task-1", migration.Task.Id);
        Assert.AreEqual("task-1", cancellation.Task.Id);
        Assert.AreEqual(
            """
            {"model_id":"model-1","summary":"manual_ui_benchmark","passed":true,"metrics":{"source":"models_page"}}
            """,
            captured[0].Body);
        Assert.AreEqual("""{"url":"https://civitai.com/models/1"}""", captured[1].Body);
        Assert.AreEqual("""{"file_path":"C:\\models\\model.safetensors","folder":"checkpoints"}""", captured[2].Body);
        Assert.AreEqual("{}", captured[3].Body);
        Assert.AreEqual("""{"task_id":"task-1"}""", captured[4].Body);
    }

    [TestMethod]
    public async Task PlannerAndReactiveLabs_UseAuthenticatedPreparedPayloadContracts()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                await request.Content!.ReadAsStringAsync(cancellationToken)));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);
        using var planner = JsonDocument.Parse(
            """{"analysis":{"basicInfo":{"title":"Signal"}},"plan":{"scenes":[{"id":"scene-1"}]},"settings":{"sceneCount":4},"apply_timeline":true,"overwrite_timeline":false}""");
        using var reactive = JsonDocument.Parse(
            """{"metadata":{"preset":"cinematic"},"keyframes":[{"frame":24}],"overwrite_motion_track":true,"overwrite_camera":false}""");

        await client.ImportPlannerLabAsync("project / one", planner.RootElement.Clone());
        await client.ApplyReactiveLabAsync("project / one", reactive.RootElement.Clone());

        Assert.HasCount(2, captured);
        Assert.IsTrue(captured.All(item => item.Method == HttpMethod.Post));
        Assert.IsTrue(captured.All(item =>
            item.Authorization is not null
            && item.Authorization.StartsWith("Bearer ", StringComparison.Ordinal)
            && item.Authorization.EndsWith("-token", StringComparison.Ordinal)));
        Assert.IsTrue(captured.All(item => item.ContentType == "application/json"));
        Assert.AreEqual(
            "/v1/projects/project%20%2F%20one/planner_lab/import",
            captured[0].Uri.AbsolutePath);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%20one/reactive_lab/apply",
            captured[1].Uri.AbsolutePath);

        using (var payload = JsonDocument.Parse(captured[0].Body))
        {
            Assert.AreEqual("Signal", payload.RootElement.GetProperty("analysis").GetProperty("basicInfo").GetProperty("title").GetString());
            Assert.AreEqual("scene-1", payload.RootElement.GetProperty("plan").GetProperty("scenes")[0].GetProperty("id").GetString());
            Assert.AreEqual(4, payload.RootElement.GetProperty("settings").GetProperty("sceneCount").GetInt32());
            Assert.IsTrue(payload.RootElement.GetProperty("apply_timeline").GetBoolean());
            Assert.IsFalse(payload.RootElement.GetProperty("overwrite_timeline").GetBoolean());
        }

        using (var payload = JsonDocument.Parse(captured[1].Body))
        {
            Assert.AreEqual("cinematic", payload.RootElement.GetProperty("metadata").GetProperty("preset").GetString());
            Assert.AreEqual(24, payload.RootElement.GetProperty("keyframes")[0].GetProperty("frame").GetInt32());
            Assert.IsTrue(payload.RootElement.GetProperty("overwrite_motion_track").GetBoolean());
            Assert.IsFalse(payload.RootElement.GetProperty("overwrite_camera").GetBoolean());
        }
    }

    [TestMethod]
    public async Task WorkspaceOperations_UseTypedEscapedBackendContracts()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken)));

            return request.RequestUri!.AbsolutePath switch
            {
                var path when path.EndsWith("/assets", StringComparison.Ordinal) =>
                    JsonResponse("""{"project_id":"project / one","assets":{"audio":[{"path":"assets/audio/track.wav"}],"refs":[{"path":"assets/refs/board.png"}]}}"""),
                var path when path.EndsWith("/health/relink", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"suggestions":[{"missing":"old.wav","candidate":"assets/audio/track.wav"}],"missing_count":1}"""),
                var path when path.EndsWith("/health/collect", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"dest":"collected","copied_count":1,"skipped_count":0,"copied":["track.wav"],"skipped":[]}"""),
                var path when path.EndsWith("/health", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"health":{"ok":false,"status":"missing_assets","issues":[{"code":"MISSING_ASSET","severity":"warning","message":"One asset is missing."}],"asset_index":{"schema_version":1,"generated_at":"now","asset_count":2,"missing_count":1,"total_bytes":42,"disk_estimate_gb":0.1,"missing":[{"path":"old.wav","reason":"not_found"}],"assets":[]},"actions":["relink"]}}"""),
                var path when path.EndsWith("/music_graph", StringComparison.Ordinal) =>
                    JsonResponse("""{"schemaVersion":1,"timebase":{"sampleRate":48000,"durationSeconds":32},"tempo":{"bpm":126,"confidence":0.9},"beats":[],"sections":[{"start":0,"end":8,"label":"intro","energy":0.4}],"stems":[],"confidenceNotes":[],"semantics":{"tags":["cinematic"]}}"""),
                var path when path.EndsWith("/live_cues", StringComparison.Ordinal) =>
                    JsonResponse("""{"schemaVersion":1,"advisory_only":true,"bpm":126,"duration_s":32,"event_count":2,"events":[],"notes":[]}"""),
                var path when path.EndsWith("/live_assets", StringComparison.Ordinal) =>
                    JsonResponse("""{"schema_version":1,"ready":true,"never_blocks_on_diffusion":true,"latency_budget_ms":16,"max_update_hz":30,"duration_s":32,"pack_count":1,"channel_count":3,"packs":[]}"""),
                var path when path.EndsWith("/template_package/export", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"package":{"schema_version":1,"payload":{"visual_dna":{"palette":"neon"}}}}"""),
                var path when path.EndsWith("/template_package/import", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"applied":["visual_dna"]}"""),
                var path when path.EndsWith("/timeline/apply_plan", StringComparison.Ordinal) =>
                    JsonResponse("""{"ok":true,"timeline":{"tracks":[]},"variant_index":2}"""),
                var path when path.EndsWith("/plan/variant", StringComparison.Ordinal) =>
                    JsonResponse(
                        """
                        {"ok":true,"variant_index":2,"plan":{"source":"planner","variants":[{"index":2,"name":"Treatment C","scenes":[{"start_s":0,"end_s":8,"prompt":"Opening","negative_prompt":"flicker","approved":true,"continuity":{"subject":"performer"}}]}]}}
                        """),
                _ => throw new InvalidOperationException(request.RequestUri.AbsolutePath),
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);
        const string projectId = "project / one";

        var assets = await client.GetProjectAssetsAsync(projectId);
        var health = await client.GetProjectHealthAsync(projectId);
        var relink = await client.GetProjectRelinkSuggestionsAsync(projectId);
        var collect = await client.CollectProjectAsync(projectId);
        var graph = await client.GetProjectMusicGraphAsync(projectId);
        var cues = await client.GetProjectLiveCuesAsync(projectId);
        var liveAssets = await client.GetProjectLiveAssetsAsync(projectId);
        var template = await client.ExportProjectTemplatePackageAsync(projectId);
        await client.ImportProjectTemplatePackageAsync(projectId, template.Package, true);
        var appliedPlan = await client.ApplyPlanToTimelineAsync(projectId, 2, false);
        using var sceneMetadata = JsonDocument.Parse("""{"approved":true,"continuity":{"subject":"performer"}}""");
        var updatedPlan = await client.UpdatePlanVariantAsync(
            projectId,
            2,
            [
                new PlanSceneDto
                {
                    StartSeconds = 0,
                    EndSeconds = 8,
                    Prompt = "Opening",
                    NegativePrompt = "flicker",
                    AdditionalData = new Dictionary<string, JsonElement>
                    {
                        ["approved"] = sceneMetadata.RootElement.GetProperty("approved").Clone(),
                        ["continuity"] = sceneMetadata.RootElement.GetProperty("continuity").Clone(),
                    },
                },
            ]);

        Assert.AreEqual("assets/audio/track.wav", assets.Assets.Audio.Single().Path);
        Assert.AreEqual("MISSING_ASSET", health.Health.Issues.Single().Code);
        Assert.AreEqual("assets/audio/track.wav", relink.Suggestions.Single().Candidate);
        Assert.AreEqual(1, collect.CopiedCount);
        Assert.AreEqual(126D, graph.Tempo.Bpm);
        Assert.AreEqual("cinematic", graph.Semantics!.Tags.Single());
        Assert.AreEqual(2, cues.EventCount);
        Assert.IsTrue(liveAssets.Ready);
        Assert.IsTrue(template.Ok);
        Assert.IsTrue(appliedPlan.Ok);
        Assert.AreEqual(2, appliedPlan.VariantIndex);
        Assert.AreEqual(0, appliedPlan.Timeline.GetProperty("tracks").GetArrayLength());
        Assert.IsTrue(updatedPlan.Ok);
        Assert.AreEqual(2, updatedPlan.VariantIndex);
        Assert.AreEqual("Treatment C", updatedPlan.Plan!.Variants.Single().Name);
        Assert.IsTrue(updatedPlan.Plan.Variants.Single().Scenes.Single().AdditionalData!["approved"].GetBoolean());
        Assert.AreEqual(
            "performer",
            updatedPlan.Plan.Variants.Single().Scenes.Single().AdditionalData!["continuity"]
                .GetProperty("subject").GetString());

        Assert.HasCount(11, captured);
        Assert.IsTrue(captured.All(item =>
            item.Authorization is not null
            && item.Authorization.StartsWith("Bearer ", StringComparison.Ordinal)
            && item.Authorization.EndsWith("-token", StringComparison.Ordinal)));
        Assert.IsTrue(captured.All(item =>
            item.Uri.AbsolutePath.StartsWith("/v1/projects/project%20%2F%20one/", StringComparison.Ordinal)));
        Assert.AreEqual(HttpMethod.Post, captured.Single(item => item.Uri.AbsolutePath.EndsWith("/health/collect", StringComparison.Ordinal)).Method);
        Assert.AreEqual("{}", captured.Single(item => item.Uri.AbsolutePath.EndsWith("/health/collect", StringComparison.Ordinal)).Body);

        using (var import = JsonDocument.Parse(captured.Single(item => item.Uri.AbsolutePath.EndsWith("/template_package/import", StringComparison.Ordinal)).Body))
        {
            Assert.IsTrue(import.RootElement.GetProperty("merge").GetBoolean());
            Assert.AreEqual(1, import.RootElement.GetProperty("package").GetProperty("schema_version").GetInt32());
            Assert.IsFalse(import.RootElement.GetProperty("package").TryGetProperty("additionalData", out _));
        }

        using (var apply = JsonDocument.Parse(captured.Single(item => item.Uri.AbsolutePath.EndsWith("/timeline/apply_plan", StringComparison.Ordinal)).Body))
        {
            Assert.AreEqual(2, apply.RootElement.GetProperty("variant_index").GetInt32());
            Assert.IsFalse(apply.RootElement.GetProperty("overwrite").GetBoolean());
        }

        using (var reorder = JsonDocument.Parse(captured.Single(item => item.Uri.AbsolutePath.EndsWith("/plan/variant", StringComparison.Ordinal)).Body))
        {
            Assert.AreEqual(2, reorder.RootElement.GetProperty("variant_index").GetInt32());
            Assert.AreEqual("Opening", reorder.RootElement.GetProperty("scenes")[0].GetProperty("prompt").GetString());
            Assert.AreEqual("flicker", reorder.RootElement.GetProperty("scenes")[0].GetProperty("negative_prompt").GetString());
            Assert.IsTrue(reorder.RootElement.GetProperty("scenes")[0].GetProperty("approved").GetBoolean());
            Assert.AreEqual(
                "performer",
                reorder.RootElement.GetProperty("scenes")[0].GetProperty("continuity")
                    .GetProperty("subject").GetString());
        }
    }

    [TestMethod]
    public async Task CloudOperations_UseAuthenticatedJsonContracts()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken)));
            return JsonResponse("""{"ok":true,"settings":{"enabled":true}}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);
        using var awsTest = JsonDocument.Parse("""{"bucket":"studio-assets"}""");
        using var awsBundle = JsonDocument.Parse("""{"bucket":"studio-assets","key":"bundles/project.zip"}""");
        using var azureTest = JsonDocument.Parse("""{"container":"model-cache","prefix":"models"}""");
        using var hfSettings = JsonDocument.Parse(
            """{"enabled":true,"bucket":"hf-cache","prefix":"weights","storage_mode":"cloud_only"}""");
        using var hfTest = JsonDocument.Parse("""{"bucket":"hf-cache","prefix":"weights"}""");
        using var lightning = JsonDocument.Parse("""{"output_dir":"lightning/bundle"}""");

        await client.TestAwsCloudAsync(awsTest.RootElement.Clone());
        await client.BundleAwsCloudAsync(awsBundle.RootElement.Clone());
        await client.TestAzureCloudAsync(azureTest.RootElement.Clone());
        await client.GetHuggingFaceCloudSettingsAsync();
        await client.SaveHuggingFaceCloudSettingsAsync(hfSettings.RootElement.Clone());
        await client.TestHuggingFaceCloudAsync(hfTest.RootElement.Clone());
        await client.BundleLightningCloudAsync(lightning.RootElement.Clone());

        Assert.HasCount(7, captured);
        Assert.IsTrue(captured.All(item =>
            item.Authorization is not null
            && item.Authorization.StartsWith("Bearer ", StringComparison.Ordinal)
            && item.Authorization.EndsWith("-token", StringComparison.Ordinal)));
        Assert.AreEqual(
            "POST /v1/cloud/aws/test|POST /v1/cloud/aws/bundle|POST /v1/cloud/azure/test|GET /v1/cloud/hf/settings|POST /v1/cloud/hf/settings|POST /v1/cloud/hf/test|POST /v1/cloud/lightning/bundle",
            string.Join("|", captured.Select(item => $"{item.Method.Method} {item.Uri.AbsolutePath}")));
        Assert.IsTrue(captured.Where(item => item.Method == HttpMethod.Post)
            .All(item => item.ContentType == "application/json"));
        Assert.AreEqual(string.Empty, captured.Single(item => item.Method == HttpMethod.Get).Body);

        using (var payload = JsonDocument.Parse(captured[1].Body))
        {
            Assert.AreEqual("studio-assets", payload.RootElement.GetProperty("bucket").GetString());
            Assert.AreEqual("bundles/project.zip", payload.RootElement.GetProperty("key").GetString());
        }

        using (var payload = JsonDocument.Parse(captured[4].Body))
        {
            Assert.IsTrue(payload.RootElement.GetProperty("enabled").GetBoolean());
            Assert.AreEqual("cloud_only", payload.RootElement.GetProperty("storage_mode").GetString());
        }

        using (var payload = JsonDocument.Parse(captured[6].Body))
        {
            Assert.AreEqual("lightning/bundle", payload.RootElement.GetProperty("output_dir").GetString());
        }
    }

    [TestMethod]
    public async Task ForgeProbes_UseAuthenticatedEscapedProjectContracts()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                string.Empty));
            return Task.FromResult(JsonResponse("""{"ok":true}"""));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("session-token"),
            httpClient);

        await client.GetAiStatusAsync();
        await client.GetComfyUiCapabilitiesAsync();
        await client.GetUnrealPreviewAsync("project / one", -3);
        await client.GetUnrealPreviewAsync("project / one", 2);
        await client.GetLiveCuePublishStatusAsync("project / one");

        Assert.HasCount(5, captured);
        Assert.IsTrue(captured.All(item => item.Method == HttpMethod.Get));
        Assert.IsTrue(captured.All(item =>
            item.Authorization is not null
            && item.Authorization.StartsWith("Bearer ", StringComparison.Ordinal)
            && item.Authorization.EndsWith("-token", StringComparison.Ordinal)));
        Assert.AreEqual("/v1/ai/status", captured[0].Uri.AbsolutePath);
        Assert.AreEqual("/v1/comfyui/capabilities", captured[1].Uri.AbsolutePath);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%20one/unreal/preview",
            captured[2].Uri.AbsolutePath);
        Assert.AreEqual("?variant_index=0", captured[2].Uri.Query);
        Assert.AreEqual("?variant_index=2", captured[3].Uri.Query);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%20one/live_cues/publish/status",
            captured[4].Uri.AbsolutePath);
    }

    [TestMethod]
    public async Task UnrealBridgeMutations_UseTypedAuthenticatedContractsAndPreserveExtensionData()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null
                    ? string.Empty
                    : await request.Content.ReadAsStringAsync(cancellationToken)));

            return request.RequestUri!.AbsolutePath switch
            {
                "/v1/projects/project%20%2F%231/export/unreal" => JsonResponse(
                    """
                    {
                      "ok": true,
                      "bundle": {
                        "bundle_dir": "outputs/unreal/hero",
                        "manifest_path": "outputs/unreal/hero/bundle_manifest.json",
                        "zip_path": "outputs/unreal/hero.zip",
                        "variant_index": 2,
                        "sequence_name": "HeroSequence",
                        "files": ["bundle_manifest.json"],
                        "future_bundle_field": "kept"
                      },
                      "future_response_field": 17
                    }
                    """),
                "/v1/projects/project%20%2F%231/unreal/import-plan" => JsonResponse(
                    """
                    {
                      "ok": true,
                      "plan_path": "outputs/unreal/hero/unreal_import_plan.json",
                      "plan": {"asset_name": "HeroAsset"},
                      "future_plan_field": true
                    }
                    """),
                "/v1/projects/project%20%2F%231/import/unreal" => JsonResponse(
                    """
                    {
                      "ok": true,
                      "imported": {
                        "bundle_dir": "outputs/unreal/hero",
                        "source_dir": "outputs/unreal/hero/returned",
                        "manifest_path": "outputs/unreal/hero/bundle_manifest.json",
                        "variant_index": 2,
                        "sequence_name": "HeroSequence",
                        "media": [{
                          "kind": "video",
                          "path": "outputs/videos/hero.mp4",
                          "source_path": "outputs/unreal/hero/returned/hero.mp4",
                          "metadata_path": "outputs/videos/hero.mp4.metadata.json",
                          "future_media_field": "kept"
                        }],
                        "future_import_field": "kept"
                      }
                    }
                    """),
                _ => throw new InvalidOperationException("Unexpected Unreal route.")
            };
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("unreal-token"),
            httpClient);

        var export = await client.ExportUnrealBundleAsync(
            " project /#1 ",
            new UnrealBundleExportRequest
            {
                VariantIndex = 2,
                BundleName = "hero",
                IncludeZip = false
            });
        var plan = await client.BuildUnrealImportPlanAsync(
            " project /#1 ",
            new UnrealImportPlanRequest
            {
                BundleDirectory = "outputs/unreal/hero",
                ContentPath = "/Game/Cinematics",
                AssetName = "HeroAsset"
            });
        var imported = await client.ImportUnrealReturnsAsync(
            " project /#1 ",
            new UnrealReturnImportRequest
            {
                BundleDirectory = "outputs/unreal/hero",
                SourceDirectory = "outputs/unreal/hero/returned"
            });

        Assert.HasCount(3, captured);
        Assert.IsTrue(captured.All(item => item.Method == HttpMethod.Post));
        Assert.IsTrue(captured.All(item => item.Authorization == "Bearer unreal-token"));
        Assert.IsTrue(captured.All(item => item.ContentType == "application/json"));

        using var exportBody = JsonDocument.Parse(captured[0].Body);
        Assert.AreEqual(2, exportBody.RootElement.GetProperty("variant_index").GetInt32());
        Assert.AreEqual("hero", exportBody.RootElement.GetProperty("bundle_name").GetString());
        Assert.IsFalse(exportBody.RootElement.GetProperty("include_zip").GetBoolean());
        Assert.AreEqual("outputs/unreal/hero", export.Bundle.BundleDirectory);
        Assert.AreEqual("kept", export.Bundle.AdditionalData!["future_bundle_field"].GetString());
        Assert.AreEqual(17, export.AdditionalData!["future_response_field"].GetInt32());

        using var planBody = JsonDocument.Parse(captured[1].Body);
        Assert.AreEqual("/Game/Cinematics", planBody.RootElement.GetProperty("content_path").GetString());
        Assert.AreEqual("HeroAsset", plan.Plan.GetProperty("asset_name").GetString());
        Assert.IsTrue(plan.AdditionalData!["future_plan_field"].GetBoolean());

        using var importBody = JsonDocument.Parse(captured[2].Body);
        Assert.AreEqual(
            "outputs/unreal/hero/returned",
            importBody.RootElement.GetProperty("source_dir").GetString());
        Assert.HasCount(1, imported.Imported.Media);
        Assert.AreEqual("outputs/videos/hero.mp4", imported.Imported.Media[0].Path);
        Assert.AreEqual("kept", imported.Imported.Media[0].AdditionalData!["future_media_field"].GetString());
        Assert.AreEqual("kept", imported.Imported.AdditionalData!["future_import_field"].GetString());
    }

    [TestMethod]
    public async Task UnrealBridgeMutations_ValidateAuthoritativeSchemaBounds()
    {
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            new HttpClient(new RecordingHandler((_, _) =>
                Task.FromResult(JsonResponse("""{"ok":true}""")))));

        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() =>
            client.ExportUnrealBundleAsync(
                "p1",
                new UnrealBundleExportRequest { VariantIndex = -1 }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.ExportUnrealBundleAsync(
                "p1",
                new UnrealBundleExportRequest { BundleName = new string('x', 121) }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.BuildUnrealImportPlanAsync(
                "p1",
                new UnrealImportPlanRequest { BundleDirectory = " " }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.BuildUnrealImportPlanAsync(
                "p1",
                new UnrealImportPlanRequest
                {
                    BundleDirectory = "outputs/unreal/hero",
                    ContentPath = new string('x', 261)
                }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.BuildUnrealImportPlanAsync(
                "p1",
                new UnrealImportPlanRequest
                {
                    BundleDirectory = "outputs/unreal/hero",
                    AssetName = new string('x', 121)
                }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.ImportUnrealReturnsAsync(
                "p1",
                new UnrealReturnImportRequest
                {
                    BundleDirectory = "outputs/unreal/hero",
                    SourceDirectory = new string('x', 261)
                }));
    }

    [TestMethod]
    public async Task RenderSceneStillsAsync_UsesEscapedAuthenticatedContractAndSerializedDefaults()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                await request.Content!.ReadAsStringAsync(cancellationToken));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("render-token"),
            httpClient);

        await client.RenderSceneStillsAsync(" project /#1 ", new RenderScenesRequest());

        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Post, captured.Method);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%231/render/stills/scenes",
            captured.Uri.AbsolutePath);
        Assert.AreEqual("Bearer " + "render" + "-token", captured.Authorization);
        Assert.AreEqual("application/json", captured.ContentType);
        using var body = JsonDocument.Parse(captured.Body);
        Assert.AreEqual(0.75, body.RootElement.GetProperty("denoise_strength").GetDouble());
        Assert.IsFalse(body.RootElement.TryGetProperty("model_id", out _));
        Assert.IsFalse(body.RootElement.TryGetProperty("seed", out _));
    }

    [TestMethod]
    public async Task ExportComfyUiWorkflowsAsync_EncodesQueryAndOmitsNullOptions()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler((request, _) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                string.Empty);
            return Task.FromResult(JsonResponse("""{"ok":true}"""));
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);
        var options = new ComfyUiWorkflowExportOptions(
            VariantIndex: -7,
            ModelId: "model /?#",
            WorkflowFamily: "img2img",
            ReferenceAsset: "refs/My Image.png",
            NegativePrompt: "bad & blurry");

        await client.ExportComfyUiWorkflowsAsync("p1", options);

        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Get, captured.Method);
        StringAssert.Contains(captured.Uri.Query, "variant_index=-7");
        StringAssert.Contains(captured.Uri.Query, "model_id=model%20%2F%3F%23");
        StringAssert.Contains(captured.Uri.Query, "reference_asset=refs%2FMy%20Image.png");
        StringAssert.Contains(captured.Uri.Query, "negative_prompt=bad%20%26%20blurry");
        Assert.IsFalse(captured.Uri.Query.Contains("source_asset=", StringComparison.Ordinal));
        Assert.IsFalse(captured.Uri.Query.Contains("seed=", StringComparison.Ordinal));
    }

    [TestMethod]
    public async Task UploadReferenceAssetAsync_UsesMultipartFileFieldAndAuthentication()
    {
        CapturedRequest? captured = null;
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured = new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                await request.Content!.ReadAsStringAsync(cancellationToken));
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("asset-token"),
            httpClient);
        var content = new TrackingStream(Encoding.UTF8.GetBytes("reference-payload"));

        await client.UploadReferenceAssetAsync(
            "p1",
            content,
            "reference image.png",
            "image/png");

        Assert.IsNotNull(captured);
        Assert.AreEqual(HttpMethod.Post, captured.Method);
        Assert.AreEqual("/v1/projects/p1/assets/refs", captured.Uri.AbsolutePath);
        Assert.AreEqual("Bearer " + "asset" + "-token", captured.Authorization);
        Assert.AreEqual("multipart/form-data", captured.ContentType);
        StringAssert.Contains(captured.Body, "name=file");
        StringAssert.Contains(captured.Body, "filename=\"reference image.png\"");
        StringAssert.Contains(captured.Body, "Content-Type: image/png");
        StringAssert.Contains(captured.Body, "reference-payload");
        Assert.IsTrue(content.IsDisposed);
    }

    [TestMethod]
    public async Task ReviewPublishingAndAdapterMethods_UseTypedAuthenticatedContractsAndDefaults()
    {
        var captured = new List<CapturedRequest>();
        using var httpClient = new HttpClient(new RecordingHandler(async (request, cancellationToken) =>
        {
            captured.Add(new CapturedRequest(
                request.Method,
                request.RequestUri!,
                request.Headers.Authorization?.ToString(),
                request.Content?.Headers.ContentType?.MediaType,
                request.Content is null
                    ? string.Empty
                    : await request.Content.ReadAsStringAsync(cancellationToken)));
            return request.RequestUri!.AbsolutePath.EndsWith("/world_adapters/export", StringComparison.Ordinal)
                ? JsonResponse("""{"ok":true,"adapter":"touchdesigner","payload":{},"simulation":{}}""")
                : request.RequestUri.AbsolutePath.EndsWith("/variant_review/decision", StringComparison.Ordinal)
                    ? JsonResponse("""{"ok":true,"variant_review":{}}""")
                    : JsonResponse("""{"ok":true,"publish":{}}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider("review-token"),
            httpClient);

        await client.SaveVariantDecisionAsync(
            " project /#1 ",
            new VariantReviewDecisionRequest
            {
                ArtifactPath = "outputs/frame 01.png",
                Decision = "cherry_picked",
                Notes = "Keep the lighting.",
                CherryPickTraits = ["lighting"],
                LockFields = ["seed"]
            });
        await client.StartLiveCuePublishAsync(" project /#1 ", new LiveCuePublishRequest());
        await client.StopLiveCuePublishAsync(" project /#1 ");
        await client.ExportWorldAdapterAsync(" project /#1 ", new WorldAdapterExportRequest());

        Assert.HasCount(4, captured);
        Assert.IsTrue(captured.All(item => item.Method == HttpMethod.Post));
        Assert.IsTrue(captured.All(item => item.Authorization == "Bearer review-token"));
        Assert.AreEqual(
            "/v1/projects/project%20%2F%231/variant_review/decision",
            captured[0].Uri.AbsolutePath);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%231/live_cues/publish/start",
            captured[1].Uri.AbsolutePath);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%231/live_cues/publish/stop",
            captured[2].Uri.AbsolutePath);
        Assert.AreEqual(string.Empty, captured[2].Body);
        Assert.AreEqual(
            "/v1/projects/project%20%2F%231/world_adapters/export",
            captured[3].Uri.AbsolutePath);

        using var decision = JsonDocument.Parse(captured[0].Body);
        Assert.AreEqual("outputs/frame 01.png", decision.RootElement.GetProperty("artifact_path").GetString());
        Assert.AreEqual("cherry_picked", decision.RootElement.GetProperty("decision").GetString());
        Assert.AreEqual("lighting", decision.RootElement.GetProperty("cherry_pick_traits")[0].GetString());
        Assert.AreEqual("seed", decision.RootElement.GetProperty("lock_fields")[0].GetString());

        using var publish = JsonDocument.Parse(captured[1].Body);
        Assert.AreEqual("127.0.0.1", publish.RootElement.GetProperty("osc_host").GetString());
        Assert.AreEqual(9000, publish.RootElement.GetProperty("osc_port").GetInt32());
        Assert.IsTrue(publish.RootElement.GetProperty("midi_enabled").GetBoolean());
        Assert.IsTrue(publish.RootElement.GetProperty("websocket_enabled").GetBoolean());
        Assert.AreEqual(1.0, publish.RootElement.GetProperty("playback_speed").GetDouble());

        using var export = JsonDocument.Parse(captured[3].Body);
        Assert.AreEqual("touchdesigner", export.RootElement.GetProperty("adapter").GetString());
        Assert.AreEqual(0, export.RootElement.GetProperty("variant_index").GetInt32());
        Assert.IsFalse(export.RootElement.TryGetProperty("sequence_name", out _));
    }

    [TestMethod]
    public async Task ReviewPublishingAndAdapterMethods_ValidateAuthoritativeSchemaBounds()
    {
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            new HttpClient(new RecordingHandler((_, _) =>
                Task.FromResult(JsonResponse("""{"ok":true}""")))));

        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.SaveVariantDecisionAsync(
                "p1",
                new VariantReviewDecisionRequest { ArtifactPath = "a.png", Decision = "maybe" }));
        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() =>
            client.StartLiveCuePublishAsync(
                "p1",
                new LiveCuePublishRequest { OscPort = 0 }));
        await Assert.ThrowsExactlyAsync<ArgumentOutOfRangeException>(() =>
            client.ExportWorldAdapterAsync(
                "p1",
                new WorldAdapterExportRequest { VariantIndex = -1 }));
        await Assert.ThrowsExactlyAsync<ArgumentException>(() =>
            client.ExportWorldAdapterAsync(
                "p1",
                new WorldAdapterExportRequest { Adapter = "unity" }));
    }

    [TestMethod]
    public async Task LiveCuePublishingMethods_PropagateCancellationToTransport()
    {
        var handlerStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        CancellationToken observedToken = default;
        using var httpClient = new HttpClient(new RecordingHandler(async (_, cancellationToken) =>
        {
            observedToken = cancellationToken;
            handlerStarted.SetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return JsonResponse("""{"ok":true,"publish":{}}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);
        using var cancellation = new CancellationTokenSource();

        var request = client.StartLiveCuePublishAsync(
            "p1",
            new LiveCuePublishRequest(),
            cancellation.Token);
        await handlerStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() => request);
        Assert.IsTrue(observedToken.CanBeCanceled);
        Assert.IsTrue(observedToken.IsCancellationRequested);
    }

    [TestMethod]
    public async Task RenderSceneStillsAsync_PropagatesCancellationToTransport()
    {
        var handlerStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        CancellationToken observedToken = default;
        using var httpClient = new HttpClient(new RecordingHandler(async (_, cancellationToken) =>
        {
            observedToken = cancellationToken;
            handlerStarted.SetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return JsonResponse("""{"ok":true}""");
        }));
        using var client = new StudioApiClient(
            new StaticEndpointProvider(new Uri("http://127.0.0.1:7863/")),
            new StaticTokenProvider(null),
            httpClient);
        using var cancellation = new CancellationTokenSource();

        var request = client.RenderSceneStillsAsync(
            "p1",
            new RenderScenesRequest(),
            cancellation.Token);
        await handlerStarted.Task.WaitAsync(TimeSpan.FromSeconds(5));
        cancellation.Cancel();

        await Assert.ThrowsAsync<OperationCanceledException>(() => request);
        Assert.IsTrue(observedToken.CanBeCanceled);
        Assert.IsTrue(observedToken.IsCancellationRequested);
    }

    private static HttpResponseMessage JsonResponse(string json, HttpStatusCode statusCode = HttpStatusCode.OK) => new(statusCode)
    {
        Content = new StringContent(json, Encoding.UTF8, "application/json")
    };

    private const string ProjectListJson =
        """{"projects":[{"id":"p1","name":"Existing","created_at":"2026-08-12 08:00:00","meta":{},"schema_version":1}]}""";

    private const string ProjectResponseJson =
        """{"project":{"id":"p1","name":"Native Project","created_at":"2026-08-12 08:00:00","meta":{},"schema_version":1},"visual_dna":{},"visual_dna_hints":{}}""";

    private const string ModelTaskActionJson =
        """{"task":{"id":"task-1","name":"Import TensorRT","status":"queued","progress":0.0,"last_log":null,"error":null,"started_at":null,"ended_at":null,"model_id":"local_sd15_tensorrt_bundle","stage":"queued","bytes_completed":0,"bytes_total":null,"files_completed":0,"files_total":null,"cancel_requested":false}}""";

    private sealed record CapturedRequest(
        HttpMethod Method,
        Uri Uri,
        string? Authorization,
        string? ContentType,
        string Body);

    private sealed class StaticEndpointProvider(Uri backendUri) : IBackendEndpointProvider
    {
        public Uri CurrentBackendUri { get; } = backendUri;
    }

    private sealed class StaticTokenProvider(string? token) : IBackendTokenProvider
    {
        public ValueTask<string?> GetTokenAsync(CancellationToken cancellationToken = default) =>
            ValueTask.FromResult(token);
    }

    private sealed class RecordingHandler(
        Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> callback) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken) =>
            callback(request, cancellationToken);
    }

    private sealed class TrackingContent : HttpContent
    {
        private readonly byte[] _bytes;

        public TrackingContent(byte[] bytes)
        {
            _bytes = bytes;
            Headers.ContentLength = bytes.Length;
            Stream = new TrackingStream(bytes);
        }

        public bool IsDisposed { get; private set; }

        public TrackingStream Stream { get; }

        protected override Task SerializeToStreamAsync(Stream stream, TransportContext? context) =>
            stream.WriteAsync(_bytes).AsTask();

        protected override bool TryComputeLength(out long length)
        {
            length = _bytes.Length;
            return true;
        }

        protected override Task<Stream> CreateContentReadStreamAsync() =>
            Task.FromResult<Stream>(Stream);

        protected override void Dispose(bool disposing)
        {
            IsDisposed = true;
            base.Dispose(disposing);
        }
    }

    private sealed class TrackingStream(byte[] bytes) : MemoryStream(bytes)
    {
        public bool IsDisposed { get; private set; }

        protected override void Dispose(bool disposing)
        {
            IsDisposed = true;
            base.Dispose(disposing);
        }
    }
}
