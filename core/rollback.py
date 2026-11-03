import shlex
import subprocess
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class RollbackReceipt:
    success: bool
    experiment_id: str
    target_command: str
    verified_state: str
    error_message: Optional[str] = None


class RollbackController:
    """Runs only the supported power-profile restore/query pair; not an experiment executor."""

    PROFILES = {"power-saver", "balanced", "performance"}

    @classmethod
    def _parse_command(cls, command: str) -> Tuple[str, ...]:
        if not isinstance(command, str) or "\x00" in command:
            raise ValueError("Command must be a NUL-free string")
        try:
            return tuple(shlex.split(command, posix=True))
        except ValueError as exc:
            raise ValueError("Malformed command string") from exc

    @classmethod
    def _validate_query(cls, query_cmd: str) -> Tuple[str, ...]:
        argv = cls._parse_command(query_cmd)
        if argv != ("powerprofilesctl", "get"):
            raise ValueError("Verification query must be exactly 'powerprofilesctl get'")
        return argv

    @classmethod
    def _validate_restore(cls, restore_cmd: str, expected_state: str) -> Tuple[str, ...]:
        argv = cls._parse_command(restore_cmd)
        if expected_state not in cls.PROFILES:
            raise ValueError("Expected state must be a supported power profile")
        if argv != ("powerprofilesctl", "set", expected_state):
            raise ValueError("Restore command must set the declared expected power profile")
        return argv

    def capture_state(self, query_cmd: str) -> str:
        """Captures the current supported power-profile state."""
        argv = self._validate_query(query_cmd)
        result = subprocess.run(
            argv, capture_output=True, text=True, check=True, timeout=5
        )
        return result.stdout.strip()

    def execute_and_verify_rollback(
        self,
        experiment_id: str,
        restore_cmd: str,
        verify_query: str,
        expected_state: str
    ) -> RollbackReceipt:
        """Restores a supported power profile and confirms it with a fresh query."""
        try:
            restore_argv = self._validate_restore(restore_cmd, expected_state)
            verify_argv = self._validate_query(verify_query)
            subprocess.run(
                restore_argv, capture_output=True, text=True, check=True, timeout=5
            )
            verify_result = subprocess.run(
                verify_argv, capture_output=True, text=True, check=True, timeout=5
            )
            current_state = verify_result.stdout.strip()
            if current_state == expected_state:
                return RollbackReceipt(
                    success=True,
                    experiment_id=experiment_id,
                    target_command=restore_cmd,
                    verified_state=current_state,
                )
            return RollbackReceipt(
                success=False,
                experiment_id=experiment_id,
                target_command=restore_cmd,
                verified_state=current_state,
                error_message=f"State mismatch: expected '{expected_state}', got '{current_state}'",
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return RollbackReceipt(
                success=False,
                experiment_id=experiment_id,
                target_command=restore_cmd,
                verified_state="FAILED",
                error_message=str(exc),
            )
