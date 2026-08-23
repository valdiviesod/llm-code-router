import pytest

from v4ld1.core.models import Complexity, Task, TaskState
from v4ld1.core.task_graph import TaskGraph, build_graph


def test_simple_task_is_not_decomposed():
    graph = build_graph(Task(prompt="fix typo", complexity=Complexity.LOW))
    assert len(graph.tasks) == 1


def test_high_complexity_decomposes_into_ordered_stages():
    graph = build_graph(Task(prompt="build auth", complexity=Complexity.HIGH))
    assert len(graph.tasks) == 4
    ready = graph.ready()
    assert len(ready) == 1, "stages are sequential, only one is ready at a time"
    ready[0].state = TaskState.DONE
    assert len(graph.ready()) == 1


def test_cycle_is_rejected():
    a = Task(prompt="a")
    b = Task(prompt="b", depends_on=[a.id])
    a.depends_on = [b.id]
    with pytest.raises(ValueError, match="cycle"):
        TaskGraph([a, b])


def test_finished_when_all_terminal():
    task = Task(prompt="x")
    graph = TaskGraph([task])
    assert not graph.finished()
    task.state = TaskState.DONE
    assert graph.finished()
