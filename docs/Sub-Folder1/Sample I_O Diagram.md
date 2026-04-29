# Sample I/O Diagram

An I/O (Input/Output) diagram visually represents the flow of data and control signals between a system (or a specific component within a system) and its external environment. It helps in understanding the interfaces and interactions without diving into the internal logic of the system.

## Purpose

*   Identify all external interfaces.
*   Distinguish between inputs and outputs.
*   Clarify data and control signal flow.
*   Aid in system design and troubleshooting.

## Key Components

*   **System Boundary:** Defines what is considered "inside" the system and what is "outside."
*   **Inputs:** Data or signals received by the system from the external environment.
*   **Outputs:** Data or signals sent by the system to the external environment.
*   **Process/System Core:** The central unit or functionality being analyzed.

## Conceptual Diagram Structure

```
+---------------------------------------+
|           [External Environment]      |
|                                       |
|  [Input Source 1]                     |
|  [Input Source 2]          ----------->  [System/Process Unit]  ----------->  [Output Destination 1]
|  [Input Source N]          ----------->                           ----------->  [Output Destination 2]
|                                                                    ----------->  [Output Destination M]
|                                       |
+---------------------------------------+
```

## Example: Automated Traffic Light System

Let's consider a simple automated traffic light system for a single intersection.

### System Boundary: Automated Traffic Light Controller

### Inputs:

*   **Vehicle Detector (North/South):** Signals presence of vehicles on North-South road. (e.g., `VEHICLE_NS_DETECTED` - Boolean)
*   **Vehicle Detector (East/West):** Signals presence of vehicles on East-West road. (e.g., `VEHICLE_EW_DETECTED` - Boolean)
*   **Pedestrian Button (North/South):** Signals a pedestrian request on N-S side. (e.g., `PED_NS_REQUEST` - Boolean)
*   **Pedestrian Button (East/West):** Signals a pedestrian request on E-W side. (e.g., `PED_EW_REQUEST` - Boolean)
*   **Timer Module:** Provides current time for sequencing. (Internal/External clock, `CURRENT_TIME` - Time value)
*   **Configuration Settings:** Stores light timings, default cycles. (e.g., `TIMER_GREEN_NS`, `TIMER_YELLOW_EW` - Integer)

### Outputs:

*   **Traffic Light (North/South):** Controls N-S traffic lights.
    *   `LIGHT_NS_RED` (Boolean)
    *   `LIGHT_NS_YELLOW` (Boolean)
    *   `LIGHT_NS_GREEN` (Boolean)
*   **Traffic Light (East/West):** Controls E-W traffic lights.
    *   `LIGHT_EW_RED` (Boolean)
    *   `LIGHT_EW_YELLOW` (Boolean)
    *   `LIGHT_EW_GREEN` (Boolean)
*   **Pedestrian Light (North/South):** Controls N-S pedestrian signals.
    *   `PED_NS_WALK` (Boolean)
    *   `PED_NS_DONT_WALK` (Boolean)
*   **Pedestrian Light (East/West):** Controls E-W pedestrian signals.
    *   `PED_EW_WALK` (Boolean)
    *   `PED_EW_DONT_WALK` (Boolean)
*   **System Status Indicator:** Diagnostic outputs. (e.g., `STATUS_OK`, `ERROR_CODE` - Boolean/Integer)

### Simplified Flow Representation:

```
+----------------------------------------------------------------------------------+
|                              Automated Traffic Light System                     |
|                                                                                  |
|   Inputs:                                         Outputs:                       |
|                                                                                  |
|   - Vehicle_NS_Detected         ------------------------------------->   - Light_NS_Red, Yellow, Green   |
|   - Vehicle_EW_Detected         |                                    |   - Light_EW_Red, Yellow, Green   |
|   - Ped_NS_Request              |           [Traffic Light           |   - Ped_NS_Walk, DontWalk         |
|   - Ped_EW_Request              |            Controller]             |   - Ped_EW_Walk, DontWalk         |
|   - Current_Time                |                                    |   - System_Status_Indicator       |
|   - Configuration_Settings      ------------------------------------->                                   |
|                                                                                  |
+----------------------------------------------------------------------------------+
```