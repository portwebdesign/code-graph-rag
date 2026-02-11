from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from codebase_rag.tests.conftest import get_node_names, run_updater


def test_csharp_error_tolerant_parsing(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    """Ensure C# parser recovers from syntax errors."""
    test_file = temp_repo / "error_sample.cs"
    test_file.write_text(
        """
namespace Demo {
    public class Broken {
        public void Bad( { }
    }
    public class ValidController {
        public void GetItem() { }
    }
}
"""
    )

    run_updater(temp_repo, mock_ingestor, skip_if_missing="c-sharp")

    created_classes = get_node_names(mock_ingestor, "Class")
    created_methods = get_node_names(mock_ingestor, "Method")

    assert any(qn.endswith(".ValidController") for qn in created_classes)
    assert any(qn.endswith(".ValidController.GetItem") for qn in created_methods)


def test_go_error_tolerant_parsing(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    """Ensure Go parser recovers from syntax errors."""
    test_file = temp_repo / "error_sample.go"
    test_file.write_text(
        """
package main

func broken( {
}

func Valid() {}
"""
    )

    run_updater(temp_repo, mock_ingestor, skip_if_missing="go")

    created_functions = get_node_names(mock_ingestor, "Function")
    assert any(qn.endswith(".Valid") for qn in created_functions)


def test_php_error_tolerant_parsing(
    temp_repo: Path,
    mock_ingestor: MagicMock,
) -> None:
    """Ensure PHP parser recovers from syntax errors."""
    test_file = temp_repo / "error_sample.php"
    test_file.write_text(
        """
<?php
class Broken {
    public function bad( {
    }
}

class ValidController {
    public function show() {
    }
}

function valid_helper() {
}
"""
    )

    run_updater(temp_repo, mock_ingestor, skip_if_missing="php")

    created_classes = get_node_names(mock_ingestor, "Class")
    created_methods = get_node_names(mock_ingestor, "Method")
    created_functions = get_node_names(mock_ingestor, "Function")

    assert any(qn.endswith(".ValidController") for qn in created_classes)
    assert any(qn.endswith(".ValidController.show") for qn in created_methods)
    assert any(qn.endswith(".valid_helper") for qn in created_functions)
