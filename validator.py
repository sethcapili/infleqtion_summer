import cirq
#Syntactic Check -- Makes sure that everything actually runs 
def preformat_code(raw_code: str) -> dict:
 
 if raw_code.startswith("```python"):
    llm_code = raw_code.removeprefix("```python").removesuffix("```").strip()
 elif raw_code.startswith("python\n"):
    llm_code = raw_code.removeprefix("python\n")
 else:
    llm_code = raw_code or ""
 return {"llm_code": llm_code}

def check_syntax(llm_code: str) -> dict:

    try:
        exec_globals = {}
        exec(llm_code, exec_globals)
        return {"passed": True, "error": None}
    except Exception as e:
        return {"passed": False, "error": str(e)}

#Structural Check -- does it have the right gates and qubits
def check_structure(llm_circuit: cirq.Circuit, control_circuit: cirq.Circuit, original_prompt: str = "", llm_code: str = "") -> dict:
    results = {}

    # Check amount of qubits
    llm_qubits = len(llm_circuit.all_qubits())
    control_qubits = len(control_circuit.all_qubits())
    results["qubit_count_match"] = llm_qubits == control_qubits

    # Check gate type present
    def get_gates(circuit):
        return {
            type(op.gate).__name__
            for moment in circuit
            for op in moment.operations
            if getattr(op, "gate", None) is not None
    }

    llm_gates = get_gates(llm_circuit)
    control_gates = get_gates(control_circuit)
    results["gate_types_match"] = control_gates.issubset(llm_gates)
    results["llm_gates"] = sorted(llm_gates)
    results["control_gates"] = sorted(control_gates)

    # Measurement Check

    llm_has_measurement = any(

        isinstance(op.gate, cirq.MeasurementGate)

        for moment in llm_circuit
        for op in moment.operations
    )

    control_has_measurement = any(
        isinstance(op.gate, cirq.MeasurementGate)
        for moment in control_circuit
        for op in moment.operations
    )
    results["measurement_match"] = llm_has_measurement == control_has_measurement

    # Simulation Check
    simulation_keywords = [
        "cirq.simulator",
        "simulator(",
        ".run(",
        ".simulate(",
        "run(",
        "simulate(",
        "sample(",
        "aer",
        "backend.run",
        "qiskit",
    ]

    def check_for_simulation(code_text: str) -> bool:
        code_lower = (code_text or "").lower()
        return any(keyword in code_lower for keyword in simulation_keywords)

    def validate_simulation_match(prompt: str, llm_code: str) -> dict:
        prompt_lower = (prompt or "").lower()
        prompt_asks_for_simulation = any(
            keyword in prompt_lower
            for keyword in [
                "simulate",
                "simulation",
                "run the circuit",
                "run circuit",
                "results",
                "execute",
                "sample",
                "measurement results",
                "output",
            ]
        )
        code_has_simulation = check_for_simulation(llm_code)
        return {
            "simulation_requested": prompt_asks_for_simulation,
            "code_contains_simulation": code_has_simulation,
            "simulation_match": (
                (prompt_asks_for_simulation and code_has_simulation)
                or (not prompt_asks_for_simulation and not code_has_simulation)
            ),
        }

    simulation_result = validate_simulation_match(original_prompt, llm_code)
    # results["simulation_requested"] = simulation_result["simulation_requested"]
    # results["code_contains_simulation"] = simulation_result["code_contains_simulation"]
    results["simulation_match"] = simulation_result["simulation_match"]

    # results["circuit_depth_llm"] = len(llm_circuit)
    # results["circuit_depth_control"] = len(control_circuit)
    results["depth_within_tolerance"] = len(llm_circuit) <= len(control_circuit) * 2

    return results

# Semantic Check - checks the output distribution
import numpy as np
import json
import cirq
from huggingface_hub import InferenceClient

def check_semantics(llm_circuit: cirq.Circuit, control_circuit: cirq.Circuit, reps: int = 1000, tolerance: float = 0.1) -> dict:
    simulator = cirq.Simulator()

    #Measurement Ensured

    def ensure_measured(circuit):
        qubits = sorted(circuit.all_qubits())
        if not any(
            isinstance(op.gate, cirq.MeasurementGate)
            for moment in circuit
            for op in moment.operations
        ):
            circuit = circuit + cirq.measure(*qubits, key='result')
        return circuit

    llm_measured = ensure_measured(llm_circuit.copy())
    control_measured = ensure_measured(control_circuit.copy())

    llm_result = simulator.run(llm_measured, repetitions=reps)
    control_result = simulator.run(control_measured, repetitions=reps)

    #Build Probability(Result) distributions

    def get_distribution(result, reps):
        counts = result.multi_measurement_histogram(keys=['result']) # counts the number of outcomes 
        return {k: v/ reps for k, v in counts.items()}  #turns number into probability
    llm_dist = get_distribution(llm_result, reps)
    control_dist = get_distribution(control_result, reps)

    #Compare distributions 

    all_keys = set(llm_dist.keys()) | set(control_dist.keys())

    distribution_match = all(
        abs(llm_dist.get(k, 0) - control_dist.get(k, 0)) <= tolerance
        for k in all_keys
    )

    # STATE VECTOR
    def get_state_vector(circuit):
        no_measurement = cirq.drop_terminal_measurements(circuit)
        return cirq.final_state_vector(no_measurement)

    llm_vector = get_state_vector(llm_circuit)
    control_vector = get_state_vector(control_circuit)

    #comparing the vectors
    fidelity = float(abs(np.dot(np.conj(llm_vector), control_vector))**2)
    assert cirq.equal_up_to_global_phase(llm_vector, control_vector)
    # np.dot(np.conj()creates inner products of the two vectors 
    # the absolute value and square on it makes the quantum fidelity 
    # quantum fidelity of 1 means that the two vectors are the same and 0 means that they are orthagonal or completely different. 
    
    # RETURN combined result
    return {
        "passed": bool(cirq.equal_up_to_global_phase(llm_vector, control_vector)),
        "fidelity_score": fidelity,
        "distribution_match": distribution_match,
        # "llm_distribution": {str(k): v for k, v in llm_dist.items()},
        # "control_distribution": {str(k): v for k, v in control_dist.items()},
    }

# THE BIG ONE

def build_scorecard(llm_code: str, control_circuit: cirq.Circuit, original_prompt: str) -> dict:
    """Create a layered scorecard matching the prompting.md validator rubric."""
    report = {
        "parse_success": False,
        "execution_success": False,
        "correct_qubits": False,
        "correct_gates": False,
        "measurement_correct": False,
        "semantic_success": False,
        "fully_correct": False,
        "verdict": "invalid_code",
        "error_categories": [],
        "reason": "",
    }

    syntax = check_syntax(llm_code)
    report["parse_success"] = syntax["passed"]
    if not syntax["passed"]:
        report["reason"] = f"Syntax error: {syntax['error']}"
        report["error_categories"] = ["invalid_code"]
        return report

    exec_globals = {}
    try:
        exec(llm_code, exec_globals)
        report["execution_success"] = True
    except Exception as exc:
        report["reason"] = f"Execution error: {exc}"
        report["error_categories"] = ["invalid_code"]
        return report

    llm_circuit = next(
        (v for v in exec_globals.values() if isinstance(v, cirq.Circuit)),
        None,
    )
    if llm_circuit is None:
        report["reason"] = "No Cirq circuit was found in the generated code."
        report["error_categories"] = ["no_circuit_found"]
        return report

    # Step 3: Structure
    structure = check_structure(
        llm_circuit,
        control_circuit,
        original_prompt=original_prompt,
        llm_code=llm_code,
    )
    report["correct_qubits"] = structure["qubit_count_match"]
    report["correct_gates"] = structure["gate_types_match"]
    report["measurement_correct"] = structure["measurement_match"]

    # Step 4: Semantics
    try:
        semantics = check_semantics(llm_circuit, control_circuit)
        report["semantic_success"] = semantics.get("passed", False)
    except Exception as exc:
        report["semantic_success"] = False
        report["error_categories"].append("semantic_fail")
        report["reason"] = f"Semantic check failed: {exc}"
        error_categories = []
    if not report["correct_qubits"]:
        error_categories.append("missing_qubits")
    if not report["correct_gates"]:
        error_categories.append("wrong_gate")
    if not report["measurement_correct"]:
        error_categories.append("missing_measurement")
    if not structure.get("simulation_match", True):
        error_categories.append("simulation_fail")
    if not report["semantic_success"]:
        error_categories.append("semantic_fail")

    report["error_categories"] = error_categories

    if not error_categories:
        report["fully_correct"] = True
        report["verdict"] = "success"
        report["reason"] = "The circuit passed syntax, structure, and semantic checks."
    else:
        report["verdict"] = error_categories[0]
        report["reason"] = "The circuit failed one or more validator checks."

    return report


def validate_circuit(llm_code: str, control_circuit: cirq.Circuit, original_prompt: str) -> dict:
    return build_scorecard(llm_code, control_circuit, original_prompt)


import json
import cirq
from huggingface_hub import InferenceClient
import os
from openai import OpenAI

def call_llm(prompt: str) -> str:
    client = OpenAI(
        api_key=os.environ["HF_TOKEN"],
        base_url="https://router.huggingface.co/v1",
    )

    completion = client.chat.completions.create(
        model="Qwen/Qwen2.5-Coder-7B-Instruct",
        messages=[
            {"role": "user", "content": prompt}
        ],
        max_tokens=1000,
    )
    return completion.choices[0].message.content

JSON_SCHEMA_INSTRUCTIONS = """Return valid JSON only. Do not include Markdown or explanations.
The JSON schema must be:
{
  "description": string,
  "framework": "cirq",
  "num_qubits": integer,
  "code": string,
  "expected_gates": list[string],
  "measurement_included": boolean,
  "expected_behavior": string
}"""

def build_initial_prompt(user_request: str) -> str:
    return f"{JSON_SCHEMA_INSTRUCTIONS}\n\nUser request:\n{user_request}"

def build_repair_prompt(original_prompt, bad_code, validation_result):
    # Mapping deterministic error categories to clear, non-negotiable feedback
    ERROR_MESSAGES = {
        "missing_qubits": "There was the wrong number of qubits within the circuit generated.",
        "simulation_fail": "Do NOT include a simulator or run/simulate commands.",
        "wrong_gate": "Missing required gates. The circuit must include the gates neccessary for the described circuit, rethink the neccessary gates.",
        "invalid_code": "The syntax is invalid or could not be parsed by Cirq. Return only valid Python in the 'code' field.",
        "missing_measurement": "Only include measurements when the prompt specifies for it.",
        "semantic_fail": "The circuit's output state/distribution does not match what the request describes. Reconsider the gate sequence.",
        "no_circuit_found": "No cirq.Circuit variable was found. Define a variable (e.g. 'circuit') holding a cirq.Circuit.",
    }

    # Format instructions based on exactly what failed
    dynamic_rules = [
        ERROR_MESSAGES[error]
        for error in validation_result["error_categories"]
        if error in ERROR_MESSAGES
    ]
    rules_text = "\n".join(f"- {rule}" for rule in dynamic_rules)

    # Construct the next prompt payload
    repair_prompt = f"""{JSON_SCHEMA_INSTRUCTIONS}

The previous code you generated failed validation.

Original user request:
{original_prompt}

Failure: {validation_result['verdict']}
Reason for failure: {validation_result['reason']}

Specific issues to fix:
{rules_text}

Bad code attempt:
```python
{bad_code}
```

Return corrected JSON only, following the schema above."""
    return repair_prompt


def parse_model_output(raw_output):
    text = raw_output.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").removesuffix("```").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").removesuffix("```").strip()
    return json.loads(text)

def run_repair_loop(original_prompt, control_circuit, max_attempts=10):
    current_prompt = build_initial_prompt(original_prompt)

    for attempt in range(max_attempts):
        print(f"--- Attempt {attempt + 1} of {max_attempts} ---")

        raw_output = call_llm(current_prompt)

        try:
            payload = parse_model_output(raw_output)
            code = payload["code"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            validation_result = {
                "fully_correct": False,
                "verdict": "invalid_code",
                "reason": f"Model output was not valid JSON with a 'code' field: {exc}",
                "error_categories": ["invalid_code"],
            }
            bad_code = raw_output
        else:
            validation_result = validate_circuit(code, control_circuit, original_prompt)
            bad_code = code

        # Step 3: Check if it passed all semantic/structural checks
        if validation_result["fully_correct"]:
            print("Validation passed successfully!")
            return bad_code

        # Step 4: If it failed, generate a new prompt using the template facts
        print(f"Validation failed. Errors: {validation_result['error_categories']}")
        current_prompt = build_repair_prompt(original_prompt, bad_code, validation_result)

    # If it exhausts all attempts without passing
    raise RuntimeError(f"Model failed to generate a valid circuit within {max_attempts} attempts.")