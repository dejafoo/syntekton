"""Interface agent-loop executor for contract inventory and simulation."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tasks import TaskResult
from product_factory.domain.usage import UsageMetrics
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
)
from product_factory.gateway.mock import MockGateway
from product_factory.schemas import validate_write_payload
from product_factory.workflows.artifacts import ROLE_SPIKE_RESULT
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.registry import is_registered_workflow


class InterfaceAgentExecutor:
    executor_mode = "interface_agent_loop"
    adapter_ids = frozenset({"interface_agent"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        run_request = request.request
        profile = request.model_profile
        package_hash = request.package_hash
        land_map = request.land_map
        composer_role = request.composer_role
        execution_mode = (
            "deterministic_mock" if request.allow_deterministic_workers else "live"
        )

        artifact_refs = []
        typed_artifacts = []
        summary = ""
        result_status: str = "success"
        tool_call_ids: list[str] = []
        model_usage = UsageMetrics()

        try:
            contract_paths = [
                str(path)
                for path in run_request.pack_input.get("contract_paths") or []
                if str(path).strip()
            ]
            if not contract_paths:
                raise ToolAuthorizationError(
                    "interface_analysis requires at least one contract path"
                )
            inventory_results: list[dict[str, Any]] = []
            for index, contract_path in enumerate(contract_paths):
                inventory = broker.execute(
                    task_id=task.id,
                    tool_name="contract_inventory",
                    arguments={"path": contract_path},
                )
                tool_call_ids.append(inventory.get("tool_call_id", ""))
                inventory_results.append(inventory)
                receipt = {
                    "schema_id": "contract_inventory.v1",
                    "role": "contract_inventory",
                    "result": inventory,
                }
                validate_write_payload("contract_inventory.v1", receipt)
                typed_artifacts.append(
                    artifacts.put_json(
                        receipt,
                        logical_name=f"contract-inventory-{index + 1}.json",
                        created_by_task_id=task.id,
                        schema_id="contract_inventory.v1",
                        schema_version="1",
                    )
                )

            if len(contract_paths) >= 2:
                comparison = broker.execute(
                    task_id=task.id,
                    tool_name="diff_contracts",
                    arguments={
                        "baseline_path": contract_paths[0],
                        "candidate_path": contract_paths[1],
                    },
                )
                tool_call_ids.append(comparison.get("tool_call_id", ""))
            else:
                comparison = {
                    "baseline_path": contract_paths[0],
                    "candidate_path": None,
                    "classification": "no_baseline",
                    "changes": [],
                    "limitations": [
                        "Only one contract was supplied; cross-version "
                        "compatibility was not measured"
                    ],
                }
            receipt = {
                "schema_id": "contract_compatibility.v1",
                "role": "contract_compatibility",
                "result": comparison,
            }
            validate_write_payload("contract_compatibility.v1", receipt)
            typed_artifacts.append(
                artifacts.put_json(
                    receipt,
                    logical_name="contract-compatibility.json",
                    created_by_task_id=task.id,
                    schema_id="contract_compatibility.v1",
                    schema_version="1",
                )
            )

            schema_name = run_request.pack_input.get("schema_name")
            first_inventory = inventory_results[0]
            if (
                not schema_name
                and first_inventory.get("kind") == "openapi"
                and first_inventory.get("schemas")
            ):
                schema_name = first_inventory["schemas"][0]
            fixture_path = f"synthetic/{task.id.lower()}-fixture.json"
            fixture_args: dict[str, Any] = {
                "contract_path": contract_paths[0],
                "output_path": fixture_path,
            }
            if schema_name:
                fixture_args["schema_name"] = schema_name
            fixture = broker.execute(
                task_id=task.id,
                tool_name="generate_synthetic_fixture",
                arguments=fixture_args,
            )
            tool_call_ids.append(fixture.get("tool_call_id", ""))
            simulation_args: dict[str, Any] = {
                "contract_path": contract_paths[0],
                "fixture_path": fixture_path,
            }
            if schema_name:
                simulation_args["schema_name"] = schema_name
            simulation = broker.execute(
                task_id=task.id,
                tool_name="run_contract_simulation",
                arguments=simulation_args,
            )
            tool_call_ids.append(simulation.get("tool_call_id", ""))
            receipt = {
                "schema_id": "contract_simulation.v1",
                "role": "contract_simulation",
                "result": {**simulation, "fixture": fixture},
            }
            validate_write_payload("contract_simulation.v1", receipt)
            typed_artifacts.append(
                artifacts.put_json(
                    receipt,
                    logical_name="contract-simulation.json",
                    created_by_task_id=task.id,
                    schema_id="contract_simulation.v1",
                    schema_version="1",
                )
            )

            evidence_output = {
                "task_id": task.id,
                "artifact_refs": [
                    {
                        **ref.model_dump(mode="json"),
                        "role": (
                            "contract_inventory"
                            if ref.schema_id == "contract_inventory.v1"
                            else (
                                "contract_compatibility"
                                if ref.schema_id == "contract_compatibility.v1"
                                else "contract_simulation"
                            )
                        ),
                    }
                    for ref in typed_artifacts
                ],
                "artifact_excerpts": [
                    {
                        "logical_name": ref.logical_name,
                        "schema_id": ref.schema_id,
                        "content": artifacts.get_text(ref.sha256),
                    }
                    for ref in typed_artifacts
                ],
            }
            workflow_type = run_request.workflow_type
            if not is_registered_workflow(workflow_type):
                raise RuntimeError(
                    f"interface_analysis requires a registered workflow pack, got {workflow_type!r}"
                )
            document_name = (
                land_map.logical_name_for(ROLE_SPIKE_RESULT, default="SPIKE_RESULT.json")
                if land_map is not None
                else "SPIKE_RESULT.json"
            )
            spike_document = handler_for(workflow_type).compose(
                composer_role or ROLE_SPIKE_RESULT,
                ComposeContext(
                    request=run_request,
                    role=composer_role or ROLE_SPIKE_RESULT,
                    document_name=document_name,
                    dependency_outputs=[evidence_output],
                    use_mock=isinstance(request.raw_gateway, MockGateway),
                ),
            )
            spike_artifact = artifacts.put_text(
                spike_document,
                media_type="application/json",
                logical_name=document_name,
                created_by_task_id=task.id,
                schema_id="spike_result.v1",
                schema_version="1",
                handoff_state="evidence_complete",
            )
            artifact_refs.extend([*typed_artifacts, spike_artifact])
            summary = "Typed interface evidence and spike result created"
        except (ToolAuthorizationError, RuntimeError, ValueError) as exc:
            artifact_refs.extend(typed_artifacts)
            result_status = "failed"
            summary = f"interface_analysis_failed: {exc}"

        tool_call_ids = [tid for tid in tool_call_ids if tid]
        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status=result_status,  # type: ignore[arg-type]
                summary=summary,
                artifact_refs=artifact_refs,
                model_profile=profile,
                resolved_model_id=profile,
                provider=getattr(
                    request.gateway, "default_model", type(request.gateway).__name__
                ),
                prompt_package_hash=package_hash,
                tool_call_ids=tool_call_ids,
                usage=model_usage,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={"typed_artifact_count": len(typed_artifacts)},
        )
