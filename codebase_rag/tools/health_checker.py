"""
This module defines the `HealthChecker` class, a tool for diagnosing the
application's environment and configuration.

It provides a series of checks to verify that all external dependencies,
services, and configurations are correctly set up. This is used by the `doctor`
CLI command to help users troubleshoot their installation.

Key functionalities:
-   Checking if the Docker daemon is running.
-   Verifying the connection to the Memgraph database.
-   Ensuring that required API keys are set in the environment or settings.
-   Checking for the presence of essential command-line tools (e.g., `rg`).
-   Aggregating the results of all checks into a summary.
"""

from __future__ import annotations

import os
import subprocess

import mgclient  # ty: ignore[unresolved-import]
from loguru import logger

from codebase_rag.core.config import settings
from codebase_rag.data_models.schemas import HealthCheckResult

from ..core import constants as cs


class HealthChecker:
    """
    Performs a series of health checks on the application's environment.
    """

    def __init__(self):
        """Initializes the HealthChecker."""
        self.results: list[HealthCheckResult] = []

    def check_docker(self) -> HealthCheckResult:
        """
        Checks if the Docker daemon is running and responsive.

        Returns:
            HealthCheckResult: The result of the Docker check.
        """
        try:
            result = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                return HealthCheckResult(
                    name=cs.HEALTH_CHECK_DOCKER_RUNNING,
                    passed=True,
                    message=cs.HEALTH_CHECK_DOCKER_RUNNING_MSG.format(version=version),
                )
            else:
                return HealthCheckResult(
                    name=cs.HEALTH_CHECK_DOCKER_NOT_RUNNING,
                    passed=False,
                    message=cs.HEALTH_CHECK_DOCKER_NOT_RESPONDING_MSG,
                    error=result.stderr.strip() or cs.HEALTH_CHECK_DOCKER_EXIT_CODE,
                )
        except FileNotFoundError:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_DOCKER_NOT_RUNNING,
                passed=False,
                message=cs.HEALTH_CHECK_DOCKER_NOT_INSTALLED_MSG,
                error=cs.HEALTH_CHECK_DOCKER_NOT_IN_PATH,
            )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_DOCKER_NOT_RUNNING,
                passed=False,
                message=cs.HEALTH_CHECK_DOCKER_TIMEOUT_MSG,
                error=cs.HEALTH_CHECK_DOCKER_TIMEOUT_ERROR,
            )
        except Exception as e:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_DOCKER_NOT_RUNNING,
                passed=False,
                message=cs.HEALTH_CHECK_DOCKER_FAILED_MSG,
                error=str(e),
            )

    def check_memgraph_connection(self) -> HealthCheckResult:
        """
        Checks if a connection can be established with the Memgraph database.

        Returns:
            HealthCheckResult: The result of the connection check.
        """
        conn = None
        cursor = None
        try:
            if settings.MEMGRAPH_USERNAME is not None:
                conn = mgclient.connect(
                    host=settings.MEMGRAPH_HOST,
                    port=settings.MEMGRAPH_PORT,
                    username=settings.MEMGRAPH_USERNAME,
                    password=settings.MEMGRAPH_PASSWORD,
                )
            else:
                conn = mgclient.connect(
                    host=settings.MEMGRAPH_HOST,
                    port=settings.MEMGRAPH_PORT,
                )

            cursor = conn.cursor()
            cursor.execute(cs.HEALTH_CHECK_MEMGRAPH_QUERY)
            list(cursor.fetchall())

            return HealthCheckResult(
                name=cs.HEALTH_CHECK_MEMGRAPH_SUCCESSFUL,
                passed=True,
                message=cs.HEALTH_CHECK_MEMGRAPH_CONNECTED_MSG.format(
                    host=settings.MEMGRAPH_HOST,
                    port=settings.MEMGRAPH_PORT,
                ),
            )

        except mgclient.MemgraphError as e:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_MEMGRAPH_FAILED,
                passed=False,
                message=cs.HEALTH_CHECK_MEMGRAPH_CONNECTION_FAILED_MSG,
                error=cs.HEALTH_CHECK_MEMGRAPH_ERROR.format(error=str(e)),
            )
        except Exception as e:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_MEMGRAPH_FAILED,
                passed=False,
                message=cs.HEALTH_CHECK_MEMGRAPH_UNEXPECTED_FAILURE_MSG,
                error=str(e),
            )
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception as e:
                    logger.warning(f"Failed to close Memgraph cursor: {e}")
            if conn is not None:
                try:
                    conn.close()
                except Exception as e:
                    logger.warning(f"Failed to close Memgraph connection: {e}")

    def check_api_key(self, env_name: str, display_name: str) -> HealthCheckResult:
        """
        Checks if a specific API key is set in the environment or settings.

        Args:
            env_name (str): The name of the environment variable for the API key.
            display_name (str): The user-friendly name of the service.

        Returns:
            HealthCheckResult: The result of the API key check.
        """
        value = os.getenv(env_name) or getattr(settings, env_name, None)
        passed = bool(value)
        error_msg = (
            None
            if passed
            else cs.HEALTH_CHECK_API_KEY_MISSING_MSG.format(env_name=env_name)
        )
        return HealthCheckResult(
            name=(
                cs.HEALTH_CHECK_API_KEY_SET.format(display_name=display_name)
                if passed
                else cs.HEALTH_CHECK_API_KEY_NOT_SET.format(display_name=display_name)
            ),
            passed=passed,
            message=cs.HEALTH_CHECK_API_KEY_CONFIGURED
            if passed
            else cs.HEALTH_CHECK_API_KEY_NOT_CONFIGURED,
            error=error_msg,
        )

    def check_api_keys(self) -> list[HealthCheckResult]:
        """
        Runs API key checks for all configured services.

        Returns:
            list[HealthCheckResult]: A list of results for each API key check.
        """
        return [
            self.check_api_key(env_name, display_name)
            for env_name, display_name in cs.HEALTH_CHECK_TOOLS
        ]

    def check_external_tool(
        self, tool_name: str, command: str | None = None
    ) -> HealthCheckResult:
        """
        Checks if an external command-line tool is installed and available in the PATH.

        Args:
            tool_name (str): The user-friendly name of the tool.
            command (str | None): The actual command to execute for the check.

        Returns:
            HealthCheckResult: The result of the tool check.
        """
        cmd = command or tool_name
        check_cmd = [
            cs.SHELL_CMD_WHERE if os.name == "nt" else cs.SHELL_CMD_WHICH,
            cmd,
        ]

        try:
            result = subprocess.run(
                check_cmd,
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if result.returncode == 0:
                path = result.stdout.strip().splitlines()[0]
                return HealthCheckResult(
                    name=cs.HEALTH_CHECK_TOOL_INSTALLED.format(tool_name=tool_name),
                    passed=True,
                    message=cs.HEALTH_CHECK_TOOL_INSTALLED_MSG.format(path=path),
                )
            else:
                return HealthCheckResult(
                    name=cs.HEALTH_CHECK_TOOL_NOT_INSTALLED.format(tool_name=tool_name),
                    passed=False,
                    message=cs.HEALTH_CHECK_TOOL_NOT_IN_PATH_MSG.format(cmd=cmd),
                    error=cs.HEALTH_CHECK_TOOL_NOT_IN_PATH_MSG.format(cmd=cmd),
                )
        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_TOOL_NOT_INSTALLED.format(tool_name=tool_name),
                passed=False,
                message=cs.HEALTH_CHECK_TOOL_TIMEOUT_MSG,
                error=cs.HEALTH_CHECK_TOOL_TIMEOUT_ERROR.format(cmd=cmd),
            )
        except Exception as e:
            return HealthCheckResult(
                name=cs.HEALTH_CHECK_TOOL_NOT_INSTALLED.format(tool_name=tool_name),
                passed=False,
                message=cs.HEALTH_CHECK_TOOL_FAILED_MSG,
                error=str(e),
            )

    def run_all_checks(self) -> list[HealthCheckResult]:
        """
        Runs all defined health checks.

        Returns:
            list[HealthCheckResult]: A list containing the results of all checks.
        """
        self.results = []
        self.results.append(self.check_docker())
        self.results.append(self.check_memgraph_connection())
        self.results.extend(self.check_api_keys())
        for tool_name, cmd in cs.HEALTH_CHECK_EXTERNAL_TOOLS:
            self.results.append(self.check_external_tool(tool_name, cmd))
        return self.results

    def get_summary(self) -> tuple[int, int]:
        """
        Gets a summary of the health check results.

        Returns:
            tuple[int, int]: A tuple containing the number of passed checks and the total number of checks.
        """
        passed = sum(1 for r in self.results if r.passed)
        return passed, len(self.results)
