# Agent-Based Operations Development Methodology

**Document ID:** EOSAT1-DEV-METHOD-001
**Issue:** 1.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The EOSAT-1 Space Mission Simulation was developed using an agent-based methodology that
mirrors the structure and processes of real spacecraft mission operations teams. Rather than
treating the simulation as a monolithic software development effort, the work was organised
around specialised agents — each representing an operator position with domain expertise,
responsibility boundaries, and a distinct operational perspective.

This approach produces a simulation that is not merely technically accurate, but
operationally authentic: the resulting system reflects the way real teams think about,
build, test, and operate spacecraft. The methodology emerges from the observation that the
best way to build an operations simulator is to develop it the way a real operations team
would prepare for a mission.

### 1.1 Core Principle

The fundamental principle is: **if each operator position would consider the simulation
indistinguishable from a real spacecraft within their domain, then the simulation is
sufficient for training purposes.**

This "undetectably different from real" criterion drives all requirements capture, fidelity
analysis, and implementation decisions.

### 1.2 Mapping to Real Operations Teams

| Agent Role          | Real Operations Equivalent      | Domain Responsibility              |
|---------------------|---------------------------------|------------------------------------|
| Flight Director     | Flight Director (FD)            | Overall authority, procedures, coordination |
| Power/Thermal       | EPS/TCS Engineer                | Power budget, thermal control, battery mgmt |
| AOCS                | AOCS Engineer                   | Attitude determination and control  |
| TTC                 | Communications Engineer         | RF link, ground stations, data rates |
| OBDH                | Data Handling / Software Eng.   | OBC health, commanding, data mgmt  |
| Payload             | Payload Operations Engineer     | Instrument ops, data quality, scheduling |

## 2. Phase A: Requirements Capture

### 2.1 Position-Based Requirements Research

In Phase A, six agents (one per operator position) independently research what their
position needs from the simulation. Each agent investigates:

1. **Telemetry requirements**: Which parameters does this position monitor in real operations?
   What update rates are needed? What are the nominal ranges and limit definitions?

2. **Command requirements**: Which commands does this position issue? What are the command
   parameters, validation rules, and expected responses?

3. **Procedure requirements**: Which operational procedures does this position execute or
   participate in? What GO/NO-GO criteria apply?

4. **Failure mode requirements**: What anomalies does this position detect and respond to?
   What are the FDIR rules and escalation paths?

5. **Display requirements**: What information layout does this position need on the MCS
   screen? What charts, indicators, and alarms are essential?

### 2.2 Research Method

Each agent follows the same research process:

1. **Literature review**: Study publicly available documentation for similar real missions
   (CubeSat operations manuals, ESA/NASA operations handbooks, PUS standards).

2. **Parameter enumeration**: List every telemetry parameter and command relevant to
   the position, with units, ranges, and operational significance.

3. **Scenario analysis**: Walk through nominal operations, contingency scenarios, and
   LEOP procedures from the position's perspective.

4. **Interface identification**: Identify cross-position dependencies (e.g., AOCS needs
   EPS to provide power status for mode decisions).

5. **Documentation**: Produce a requirements document structured by subsystem, with
   traceability to the operational use case.

### 2.3 Output

Phase A produces 6 position-specific requirements documents, one per agent. These are
stored in `docs/ops_research/` and serve as the reference for subsequent phases.

## 3. Phase B: Fidelity Analysis

### 3.1 "Undetectably Different" Criterion

Phase B evaluates the current simulator fidelity against the "undetectably different from
real" standard. Each agent analyses their subsystem and answers the question: "If I were
sitting at this console during a training exercise, would I notice that this is not a real
spacecraft?"

### 3.2 Subsystem Fidelity Dimensions

Each subsystem is evaluated across multiple fidelity dimensions:

| Dimension              | Assessment Question                                    |
|------------------------|--------------------------------------------------------|
| Parameter dynamics     | Do telemetry parameters change at realistic rates?     |
| Mode transitions       | Are mode changes triggered by correct conditions?      |
| Command responses      | Does the simulator respond correctly to each command?  |
| Failure injection      | Can realistic failure scenarios be introduced?         |
| Cross-coupling         | Are inter-subsystem effects modelled (e.g., EPS-TCS)?  |
| Timing                 | Are time constants (warm-up, cooldown, delays) realistic? |
| Edge cases             | Are boundary conditions handled (eclipse, low battery)?  |

### 3.3 Fidelity Gap Identification

For each dimension where the simulator falls short of the "undetectably different"
standard, the agent documents:

- **Current behaviour**: What the simulator does now.
- **Required behaviour**: What would be expected from a real spacecraft.
- **Gap severity**: Critical (would immediately break immersion), Moderate (noticeable
  after extended operation), or Minor (only a specialist would notice).
- **Implementation effort**: Estimated complexity to close the gap.

### 3.4 Output

Phase B produces 6 fidelity analysis reports, one per subsystem. These are stored in
`docs/sim_fidelity/` and feed directly into the gap analysis phase.

## 4. Phase C: Gap Analysis

### 4.1 Cross-Referencing Requirements Against Implementation

Phase C assigns review agents to compare the Phase A requirements against the Phase B
fidelity assessments and the current codebase. The gap analysis identifies:

1. **Missing features**: Requirements that have no corresponding implementation.
2. **Incomplete features**: Partially implemented requirements (e.g., a command exists
   but does not correctly modify the simulated state).
3. **Configuration errors**: Parameters, limits, or constants that do not match the
   mission specification.
4. **Cross-subsystem gaps**: Interactions between subsystems that are not correctly
   modelled (e.g., attitude-dependent power generation).

### 4.2 Prioritisation

Gaps are prioritised using three criteria:

| Priority | Criteria                                                    |
|----------|-------------------------------------------------------------|
| P1       | Blocks realistic operations training (must fix)             |
| P2       | Reduces realism but workarounds exist (should fix)          |
| P3       | Nice-to-have fidelity improvement (could fix)              |

### 4.3 Output

Phase C produces gap analysis reports stored in `docs/gap_analysis/`. Each gap is
tracked with its priority, the affected subsystem(s), and the proposed resolution
approach.

## 5. Implementation Waves

### 5.1 Wave Structure

Implementation is organised into sequential waves, each addressing a coherent set of
gaps. The wave structure follows a dependency order that ensures each wave builds on a
stable foundation from previous waves:

| Wave | Focus                    | Rationale                                      |
|------|--------------------------|------------------------------------------------|
| 1    | Config foundation        | Fix configuration errors, establish correct parameters |
| 2    | Simulator engine         | Core simulation loop, tick processing, PUS services |
| 3    | Subsystem models         | Individual subsystem fidelity (EPS, AOCS, TCS, etc.) |
| 4    | Cross-subsystem coupling | Inter-subsystem effects (attitude-power, thermal-solar) |
| 5    | MCS UI enhancements      | Operator displays, charts, alarms, command builder |
| 6    | Planner integration      | Pass scheduling, power/data budgets, target planning |
| 7    | Contingency scenarios    | Failure injection, contingency procedures, FDIR validation |
| 8    | Test expansion           | Test coverage for all new features and interactions |
| 9    | Integration testing      | End-to-end scenario testing across all subsystems |
| 10   | Documentation            | Operations manual updates, methodology documentation |

### 5.2 Dependency Management

The wave order is not arbitrary — it reflects real dependencies:

- **Config before engine**: The engine needs correct parameters to operate on.
- **Engine before subsystems**: Subsystem models need a functioning tick loop and
  PUS service framework.
- **Subsystems before cross-coupling**: Individual subsystem models must work correctly
  before inter-subsystem effects can be validated.
- **Models before UI**: The MCS displays need correct telemetry to visualise.
- **Models before scenarios**: Contingency scenarios require the nominal model to be
  correct before failure modes can be meaningful.
- **Everything before tests**: Tests validate the full implementation stack.
- **Tests before docs**: Documentation should describe the verified implementation.

### 5.3 Wave Completion Criteria

Each wave has explicit completion criteria:

1. All planned features implemented.
2. All existing tests still pass (regression check).
3. New tests written for new features.
4. Test count at or above the wave checkpoint target.
5. No known P1 gaps remaining for that wave's scope.

## 6. Parallel Execution

### 6.1 When to Parallelise

Multiple agents can work in parallel when their tasks are independent:

| Safe to Parallelise                              | Must Serialise                          |
|--------------------------------------------------|-----------------------------------------|
| Different subsystem models (EPS, AOCS)           | Multiple agents editing the same file   |
| Different test files for different subsystems     | Feature that depends on another's output|
| Independent config files (parameters, limits)     | Index/manifest files (procedure_index)  |
| Documentation for different manual sections       | Shared utility modules (common package) |

### 6.2 File Conflict Avoidance

The primary risk of parallel execution is file conflicts. Strategies to mitigate this:

1. **Subsystem isolation**: Each subsystem model is typically in its own file, allowing
   parallel editing without conflicts.
2. **Deferred index updates**: Index files (like `procedure_index.yaml`) are updated in
   a single consolidation pass after all agents complete, rather than having each agent
   update the index.
3. **Lock-step file editing**: When multiple agents must edit the same file (e.g.,
   `engine.py` for new PUS services), they are serialised or their changes are
   consolidated into a single editing session.

### 6.3 Dependency Management Between Agents

When Agent B depends on Agent A's output:

1. Agent A completes its task and signals completion.
2. Agent B reads Agent A's output files before beginning its task.
3. This ensures Agent B works with the correct, up-to-date state.

A common pattern is "fan-out/fan-in": multiple subsystem agents work in parallel
(fan-out), then a consolidation agent merges their results into shared files (fan-in).

## 7. Verification Strategy

### 7.1 Test-First Approach

Each wave begins by reviewing the current test count and establishing a target for the
wave's completion. The test suite serves as the primary verification mechanism:

| Checkpoint  | Expected Test Count | Notes                               |
|-------------|---------------------|-------------------------------------|
| Baseline    | ~500                | Starting point before Wave 1        |
| Wave 2      | ~550                | Engine and PUS service tests        |
| Wave 4      | ~600                | Subsystem and cross-coupling tests  |
| Wave 8      | ~680                | Scenario and integration tests      |
| Wave 10     | ~691                | Full suite with documentation tests |

### 7.2 Test Categories

| Category          | Location                  | Purpose                              |
|-------------------|---------------------------|--------------------------------------|
| Unit tests        | `tests/test_common/`      | Individual module validation         |
| Simulator tests   | `tests/test_simulator/`   | Subsystem model verification         |
| MCS tests         | `tests/test_mcs/`         | UI and command processing tests      |
| Gateway tests     | `tests/test_gateway/`     | API and WebSocket tests              |
| Planner tests     | `tests/test_planner/`     | Scheduling and budget tests          |
| Integration tests | `tests/test_integration/` | End-to-end scenario tests            |

### 7.3 Known Issues Tracking

Some test failures are expected and tracked as `xfail` (expected failure):

- Configuration inconsistencies that are known but deferred.
- Features under active development.
- Platform-specific behaviours.

The `xfail` count should remain stable or decrease across waves — it should never increase
without explicit justification.

### 7.4 Regression Protection

Every wave must pass the full test suite before proceeding. If a wave introduces regressions:

1. The regression is fixed before any new features are added.
2. The root cause is documented to prevent recurrence.
3. Additional tests are written to cover the previously unprotected case.

## 8. Mapping to Real Mission Development

The agent-based development methodology maps directly to real spacecraft mission
development lifecycle phases:

| Agent Methodology Phase          | Real Mission Phase                    | Equivalent Activity           |
|----------------------------------|---------------------------------------|-------------------------------|
| Phase A: Requirements Capture    | Phase A/B: Mission Analysis           | Operations concept definition |
| Phase B: Fidelity Analysis       | Phase B: Preliminary Design Review    | Design vs. requirements check |
| Phase C: Gap Analysis            | Phase C: Critical Design Review       | Detailed gap assessment       |
| Waves 1-3: Foundation/Engine     | Phase C/D: Detailed Design            | Subsystem design and build    |
| Waves 4-6: Integration           | Phase D: Assembly, Integration, Test  | System integration            |
| Waves 7-8: Scenarios/Testing     | Phase D/E: Validation                 | Validation and acceptance     |
| Waves 9-10: Integration/Docs     | Phase E: Commissioning Readiness      | Ops readiness review          |

### 8.1 Requirements Review Equivalent

The Phase A requirements documents serve the same purpose as a mission's Operations
Requirements Review (ORR). Each agent's position-specific requirements document is
analogous to a subsystem requirements specification reviewed by the responsible engineer.

### 8.2 PDR/CDR Equivalent

The fidelity analysis (Phase B) and gap analysis (Phase C) together form the equivalent
of Preliminary and Critical Design Reviews. The gap prioritisation ensures that the most
impactful issues are addressed first, just as a CDR action items list prioritises
design changes.

### 8.3 Integration and Test Equivalent

The wave-based implementation with growing test counts mirrors the Assembly, Integration,
and Test (AIT) phase of a real spacecraft programme. Each wave adds capability and
verifies it before proceeding, just as a real AIT campaign integrates subsystems one at
a time with testing at each step.

### 8.4 Operations Readiness Review Equivalent

The final documentation waves (including this methodology document and the operations
manual updates) correspond to the Operations Readiness Review (ORR), where the operations
team confirms that all procedures, displays, training materials, and contingency plans
are in place before the mission begins.

## 9. Lessons Learned

### 9.1 Consolidate Agents Editing the Same File

**Problem**: When multiple agents independently edit the same file (e.g., `engine.py` to
add different PUS service handlers), merge conflicts arise and changes can be lost.

**Solution**: Designate a single agent to make all edits to shared files, or collect all
planned changes and apply them in a single consolidated editing session. This is
especially important for:
- The simulator engine core (`engine.py`)
- Shared dispatch tables (`service_dispatch.py`)
- Index files (`procedure_index.yaml`)
- The MCS HTML file (`index.html`)

### 9.2 Defer Index Updates

**Problem**: If each agent updates an index file (like `procedure_index.yaml`) after
creating their procedures, each update overwrites the previous agent's additions.

**Solution**: Create all procedure files first (in parallel if desired), then perform a
single index update pass that discovers and indexes all new files. This "write-then-index"
pattern avoids conflicts and ensures completeness.

### 9.3 Config-First Approach Pays Off

**Problem**: Implementing simulator features without first fixing configuration errors
leads to confusing test failures where the code is correct but the data is wrong.

**Solution**: Always fix configuration issues (parameters, limits, cross-references)
before implementing new features that depend on those configurations. The first wave
should always address known config bugs.

### 9.4 Cross-Subsystem Effects Are the Hardest Part

**Problem**: Individual subsystem models are relatively straightforward to implement
and test in isolation. The complexity arises from inter-subsystem coupling (e.g., solar
panel orientation affects both power generation and thermal environment).

**Solution**: Implement and test subsystem models independently first, then add
cross-coupling effects as a separate wave. This allows clear attribution of bugs:
if a subsystem test passes in isolation but fails with coupling enabled, the bug is
in the coupling logic.

### 9.5 Operational Authenticity Requires Operational Thinking

**Problem**: A software engineer implementing a spacecraft simulator tends to think in
terms of software architecture (classes, APIs, data flow). This produces a technically
correct but operationally unfamiliar system.

**Solution**: The agent-based approach forces operational thinking: "What would the
Power/Thermal operator see and do in this situation?" This produces displays, procedures,
and responses that feel natural to operations-trained users.

### 9.6 Test Count as a Health Metric

**Problem**: Without a quantitative metric, it is difficult to assess whether a wave
has been adequately verified.

**Solution**: Track the test count at each wave checkpoint. The count should monotonically
increase. A wave that does not add tests is suspect — either the features are untested,
or the wave did not add meaningful functionality.

### 9.7 Documentation as Final Verification

**Problem**: Documentation is often treated as an afterthought, written after the code
is "done." This misses an important verification opportunity.

**Solution**: Writing the operations manual forces a thorough review of the implemented
functionality. If a manual section cannot be written accurately because the feature does
not work as described, that is a bug — not a documentation issue. The documentation wave
often catches subtle issues that testing missed.

### 9.8 Position-Based Access Control Validates Role Boundaries

**Problem**: Without clear role boundaries, every operator position has access to every
command and display, which is unrealistic and can lead to confusion during training.

**Solution**: Implementing position-based tab filtering, command filtering, and overview
subsystem filtering forces explicit definition of "who sees what and who can do what."
This validates the role definitions from Phase A and ensures the simulation respects
operational authority boundaries.

---

*This document was generated with AI assistance. Source: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

---

*End of Document — EOSAT1-DEV-METHOD-001*
