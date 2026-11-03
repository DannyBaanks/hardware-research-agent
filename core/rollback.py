import subprocess
from typing import Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class RollbackReceipt:
    success: bool
    experiment_id: str
    target_command: str
    verified_state: str
    error_message: Optional[str] = None

class RollbackController:
    """
    Independent rollback supervisor.
    Ensures that any intervention can be reverted and verified.
    """

    def capture_state(self, query_cmd: str) -> str:
        """Runs the verification query and records the baseline value."""
        try:
            res = subprocess.run(query_cmd, shell=True, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except Exception as e:
            return f"ERROR: {e}"

    def execute_and_verify_rollback(
        self,
        experiment_id: str,
        restore_cmd: str,
        verify_query: str,
        expected_state: str
    ) -> RollbackReceipt:
        """Executes rollback command and confirms post-restoration state matches expected value."""
        try:
            # Execute restore command
            subprocess.run(restore_cmd, shell=True, capture_output=True, text=True, check=True)
            
            # Verify state
            verify_res = subprocess.run(verify_query, shell=True, capture_output=True, text=True, check=True)
            current_state = verify_res.stdout.strip()

            if current_state == expected_state:
                return RollbackReceipt(
                    success=True,
                    experiment_id=experiment_id,
                    target_command=restore_cmd,
                    verified_state=current_state
                )
            else:
                return RollbackReceipt(
                    success=False,
                    experiment_id=experiment_id,
                    target_command=restore_cmd,
                    verified_state=current_state,
                    error_message=f"State mismatch: expected '{expected_state}', got '{current_state}'"
                )
        except Exception as e:
            return RollbackReceipt(
                success=False,
                experiment_id=experiment_id,
                target_command=restore_cmd,
                verified_state="FAILED",
                error_message=str(e)
            )
