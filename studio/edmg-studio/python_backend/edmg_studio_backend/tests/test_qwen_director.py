import pytest

from edmg_studio_backend.domain.director_scene import DirectorDocument
from edmg_studio_backend.services.qwen_director import planning_messages, validate_proposal


def document():
    return DirectorDocument.model_validate(
        {
            "scenes": [
                {
                    "scene_id": "one",
                    "start_sample": "9007199254740993",
                    "end_sample": "9007199254788993",
                    "intent": "Walk through the forest",
                    "subjects": [{"id": "traveler", "appearance_notes": ["red coat"]}],
                }
            ]
        }
    )


def test_valid_proposal_is_a_draft_and_does_not_mutate_source():
    original = document()
    proposal = original.model_copy(deep=True)
    proposal.scenes[0].actions = ["walks across the stream"]
    accepted = validate_proposal(proposal.model_dump_json(), original)
    assert accepted.scenes[0].actions == ["walks across the stream"]
    assert original.scenes[0].actions == []
    assert "9007199254740993" in planning_messages(original, "Add motion")[1]["content"][0]["text"]


@pytest.mark.parametrize("change", ["timing", "bible", "identity", "analysis", "scene_set"])
def test_model_cannot_override_approved_project_constraints(change):
    original = document()
    proposal = original.model_copy(deep=True)
    if change == "timing":
        proposal.scenes[0].end_sample = "9007199254788994"
    if change == "bible":
        proposal.story_bible.visual_style = "different"
    if change == "identity":
        proposal.scenes[0].subjects[0].appearance_notes = ["blue coat"]
    if change == "analysis":
        proposal.analysis_revision = 2
    if change == "scene_set":
        proposal.scenes = []
    with pytest.raises(ValueError):
        validate_proposal(proposal.model_dump_json(), original)
