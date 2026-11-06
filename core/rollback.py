import shlex
import subprocess
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from .cpu_power import monitored_state

# The rollback status is derived from observations, never declared by a caller:
# nothing changed, it was changed and restored, or it differs and stands.
ROLLBACK_VERIFIED_UNCHANGED = "VERIFIED_UNCHANGED"
ROLLBACK_RESTORED_VERIFIED = "RESTORED_VERIFIED"
ROLLBACK_FAILED_STATE_DIFF = "FAILED_STATE_DIFF"
ROLLBACK_STATUSES = (
    ROLLBACK_VERIFIED_UNCHANGED,
    ROLLBACK_RESTORED_VERIFIED,
    ROLLBACK_FAILED_STATE_DIFF,
)


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


class RollbackContract:
    """The SAFETY_CONTRACTS.md 4 interface over the monitored CPU power surface.

    Restoration is only possible when the caller declares the exact command and
    expected state, so a campaign that declares no intervention can prove the
    surface is unchanged while having nothing to restore.
    """

    def __init__(
        self,
        sysfs_root: str = "",
        restoration_command: Optional[str] = None,
        expected_restored_state: Optional[str] = None,
        verification_query: Optional[str] = None,
        experiment_id: str = "",
    ):
        self.sysfs_root = sysfs_root
        self.restoration_command = restoration_command
        self.expected_restored_state = expected_restored_state
        self.verification_query = verification_query
        self.experiment_id = experiment_id
        self.observed_state: Optional[Dict[str, object]] = None
        self.observation_error: Optional[str] = None
        self.restoration_receipt: Optional[RollbackReceipt] = None
        self._controller = RollbackController()

    def _observe(self) -> Dict[str, object]:
        try:
            self.observed_state = monitored_state(self.sysfs_root)
        except (OSError, ValueError) as exc:
            self.observed_state = None
            self.observation_error = str(exc)
            raise
        self.observation_error = None
        return self.observed_state

    def capture_prestate(self) -> Dict[str, object]:
        """Captures the exact state before any intervention, starting a new cycle."""
        self.restoration_receipt = None
        self.observation_error = None
        return self._observe()

    def execute_rollback(self, prestate: Dict[str, object]) -> bool:
        """Runs the declared restorative command, if the campaign declared one."""
        self.restoration_receipt = None
        if self.restoration_command is None:
            return False
        self.restoration_receipt = self._controller.execute_and_verify_rollback(
            self.experiment_id,
            self.restoration_command,
            self.verification_query,
            self.expected_restored_state,
        )
        return self.restoration_receipt.success

    def verify_restoration(self, prestate: Dict[str, object]) -> bool:
        """Re-reads the monitored nodes and reports whether they match the prestate."""
        return self._observe() == prestate
