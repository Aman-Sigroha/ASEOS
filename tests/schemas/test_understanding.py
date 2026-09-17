from runtime.schemas.understanding import TaskUnderstanding


def test_task_understanding_creation():
    understanding = TaskUnderstanding(
        goal="Fix the login timeout bug",
        expected_outcome="Authentication handles timeout correctly",
        constraints=["Do not break existing authentication"],
        verification_requirements=["Run authentication tests"],
    )

    assert understanding.goal == "Fix the login timeout bug"
    assert understanding.expected_outcome == "Authentication handles timeout correctly"
    assert len(understanding.constraints) == 1
    assert len(understanding.verification_requirements) == 1


def test_task_understanding_defaults():
    understanding = TaskUnderstanding(
        goal="Fix the bug",
        expected_outcome="Tests pass",
    )

    assert understanding.constraints == []
    assert understanding.verification_requirements == []
