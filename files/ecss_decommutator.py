#!/usr/bin/env python3
"""
ECSS Space Packet Decommutator
Parses and extracts data from ECSS-E-ST-70-41C compliant space packets
"""

import struct
import json
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field
from enum import IntEnum
import logging
from datetime import datetime, timedelta

class PUSService(IntEnum):
    """PUS Service Types (ECSS-E-ST-70-41C)"""
    TC_VERIFICATION = 1
    DEVICE_ACCESS = 2
    HOUSEKEEPING = 3
    PARAMETER_STATISTICS = 4
    EVENT_REPORTING = 5
    MEMORY_MANAGEMENT = 6
    FUNCTION_MANAGEMENT = 8
    TIME_MANAGEMENT = 9
    MONITORING_AND_CONTROL = 12
    LARGE_DATA_TRANSFER = 13
    REAL_TIME_FORWARDING = 14
    ON_BOARD_STORAGE = 15
    EVENT_ACTION = 19
    PARAMETER_MANAGEMENT = 20
    REQUEST_SEQUENCING = 21
    POSITION_BASED_SCHEDULING = 22
    FILE_MANAGEMENT = 23

class VerificationSubtype(IntEnum):
    """Service 1 - TC Verification Subtypes"""
    ACCEPTANCE_SUCCESS = 1
    ACCEPTANCE_FAILURE = 2
    START_SUCCESS = 3
    START_FAILURE = 4
    PROGRESS_SUCCESS = 5
    PROGRESS_FAILURE = 6
    COMPLETION_SUCCESS = 7
    COMPLETION_FAILURE = 8

class EventSeverity(IntEnum):
    """Service 5 - Event Reporting Severity Levels"""
    INFO = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4

@dataclass
class PUSDataField:
    """Base class for PUS service-specific data fields"""
    service_type: int
    service_subtype: int
    raw_data: bytes

@dataclass
class VerificationData(PUSDataField):
    """Service 1 - TC Verification Data"""
    request_id: int
    error_code: Optional[int] = None
    error_data: Optional[bytes] = None

@dataclass
class DeviceAccessData(PUSDataField):
    """Service 2 - Device Access Data"""
    device_id: int
    raw_device_data: bytes

@dataclass
class HousekeepingData(PUSDataField):
    """Service 3 - Housekeeping Data"""
    structure_id: int
    parameters: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ParameterStatisticsData(PUSDataField):
    """Service 4 - Parameter Statistics Data"""
    parameter_id: int
    statistics_type: int
    sampling_interval: int
    value: Union[int, float]
    timestamp: Optional[datetime] = None
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    mean_value: Optional[Union[int, float]] = None

@dataclass
class EventData(PUSDataField):
    """Service 5 - Event Reporting Data"""
    event_id: int
    severity: EventSeverity
    timestamp: datetime
    auxiliary_data: Optional[bytes] = None

@dataclass
class MemoryData(PUSDataField):
    """Service 6 - Memory Management Data"""
    memory_id: int
    start_address: int
    length: int
    checksum: Optional[int] = None
    data: Optional[bytes] = None

@dataclass
class FunctionData(PUSDataField):
    """Service 8 - Function Management Data"""
    function_id: int
    arguments: List[Any] = field(default_factory=list)

@dataclass
class TimeData(PUSDataField):
    """Service 9 - Time Management Data"""
    time_format: int
    time_value: datetime
    correlation_time: Optional[datetime] = None

@dataclass
class MonitoringData(PUSDataField):
    """Service 12 - Monitoring and Control Data"""
    parameter_id: int
    check_type: int
    check_status: int
    limit_values: List[Union[int, float]] = field(default_factory=list)
    current_value: Optional[Union[int, float]] = None
    violation_count: Optional[int] = None

@dataclass
class LargeDataTransferData(PUSDataField):
    """Service 13 - Large Data Transfer Data"""
    transfer_id: int
    part_number: int
    total_parts: int
    data_part: bytes

@dataclass
class ForwardingData(PUSDataField):
    """Service 14 - Real-time Forwarding Data"""
    virtual_channel_id: int
    forwarded_data: bytes

@dataclass
class StorageData(PUSDataField):
    """Service 15 - On-board Storage Data"""
    storage_id: int
    start_time: datetime
    end_time: datetime
    stored_data: Optional[bytes] = None

@dataclass
class EventActionData(PUSDataField):
    """Service 19 - Event-Action Data"""
    event_definition_id: int
    action_definition_id: int
    enabled: bool

@dataclass
class ParameterManagementData(PUSDataField):
    """Service 20 - Parameter Management Data"""
    parameter_id: int
    parameter_value: Union[int, float, str]
    validity_flag: bool

@dataclass
class SequencingData(PUSDataField):
    """Service 21 - Request Sequencing Data"""
    sequence_id: int
    execution_time: datetime
    sequence_data: bytes

@dataclass
class SchedulingData(PUSDataField):
    """Service 22 - Position-based Scheduling Data"""
    schedule_id: int
    position_data: Dict[str, float]  # lat, lon, alt, etc.
    execution_conditions: Dict[str, Any]

@dataclass
class FileManagementData(PUSDataField):
    """Service 23 - File Management Data"""
    file_id: int
    file_path: str
    file_size: Optional[int] = None
    file_data: Optional[bytes] = None
    operation_type: Optional[str] = None
    file_attributes: Dict[str, Any] = field(default_factory=dict)

@dataclass 
class CommandRequest:
    """Structure for building telecommands"""
    apid: int
    service_type: int
    service_subtype: int
    data: bytes = b''
    sequence_count: int = 0
    ack_flags: int = 0b1111  # Request all acknowledgments by default

@dataclass
class CommandBuilder:
    """Helper class for building PUS commands"""
    apid: int
    sequence_count: int = 0
    
    def increment_sequence(self) -> int:
        """Increment and return sequence count"""
        self.sequence_count = (self.sequence_count + 1) & 0x3FFF
        return self.sequence_count

class PacketType(IntEnum):
    """ECSS Packet Types"""
    TELEMETRY = 0
    COMMAND = 1

class SecondaryHeaderFlag(IntEnum):
    """Secondary Header Flag values"""
    NOT_PRESENT = 0
    PRESENT = 1

@dataclass
class PacketHeader:
    """ECSS Space Packet Primary Header"""
    packet_version: int
    packet_type: PacketType
    secondary_header_flag: SecondaryHeaderFlag
    apid: int
    sequence_flags: int
    sequence_count: int
    data_length: int

@dataclass
class SecondaryHeader:
    """ECSS Space Packet Secondary Header (when present)"""
    spare: int
    pus_version: int
    service_type: int
    service_subtype: int
    destination_id: Optional[int] = None
    time_reference_status: Optional[int] = None
    service_type_id: Optional[int] = None

@dataclass
class DecommutatedPacket:
    """Complete decommutated space packet"""
    header: PacketHeader
    secondary_header: Optional[SecondaryHeader]
    data_field: bytes
    parsed_data: Optional[PUSDataField]
    packet_error_control: Optional[bytes]
    raw_data: bytes

class ECSSDecommutator:
    """ECSS Space Packet Decommutator with PUS Service Support"""
    
    def __init__(self, enable_logging: bool = False, time_epoch: datetime = None):
        self.logger = logging.getLogger(__name__)
        if enable_logging:
            logging.basicConfig(level=logging.INFO)
        
        # Default epoch for time conversion (J2000.0)
        self.time_epoch = time_epoch or datetime(2000, 1, 1, 12, 0, 0)
    
    def parse_cuc_time(self, data: bytes, offset: int = 0) -> datetime:
        """Parse CCSDS Unsegmented Time Code (CUC) format"""
        if len(data) < offset + 4:
            raise ValueError("Insufficient data for CUC time")
        
        # Basic CUC: 4 bytes seconds since epoch
        seconds = struct.unpack('>I', data[offset:offset+4])[0]
        return self.time_epoch + timedelta(seconds=seconds)
    
    def parse_cds_time(self, data: bytes, offset: int = 0) -> datetime:
        """Parse CCSDS Day Segmented Time Code (CDS) format"""
        if len(data) < offset + 6:
            raise ValueError("Insufficient data for CDS time")
        
        # Day number (2 bytes) + milliseconds (4 bytes)
        day_num, ms = struct.unpack('>HI', data[offset:offset+6])
        base_date = datetime(1958, 1, 1)  # CCSDS epoch
        return base_date + timedelta(days=day_num, milliseconds=ms)
    
    def parse_service_1_verification(self, data: bytes, subtype: int) -> VerificationData:
        """Parse Service 1 - TC Verification data"""
        if len(data) < 2:
            raise ValueError("Insufficient data for verification service")
        
        request_id = struct.unpack('>H', data[:2])[0]
        
        error_code = None
        error_data = None
        
        # Failure reports include error information
        if subtype in [VerificationSubtype.ACCEPTANCE_FAILURE, 
                      VerificationSubtype.START_FAILURE,
                      VerificationSubtype.PROGRESS_FAILURE,
                      VerificationSubtype.COMPLETION_FAILURE]:
            if len(data) >= 4:
                error_code = struct.unpack('>H', data[2:4])[0]
                error_data = data[4:]
        
        return VerificationData(
            service_type=1,
            service_subtype=subtype,
            raw_data=data,
            request_id=request_id,
            error_code=error_code,
            error_data=error_data
        )
    
    def parse_service_2_device_access(self, data: bytes, subtype: int) -> DeviceAccessData:
        """Parse Service 2 - Device Access data"""
        if len(data) < 2:
            raise ValueError("Insufficient data for device access service")
        
        device_id = struct.unpack('>H', data[:2])[0]
        raw_device_data = data[2:]
        
        return DeviceAccessData(
            service_type=2,
            service_subtype=subtype,
            raw_data=data,
            device_id=device_id,
            raw_device_data=raw_device_data
        )
    
    def parse_service_3_housekeeping(self, data: bytes, subtype: int) -> HousekeepingData:
        """Parse Service 3 - Housekeeping data"""
        if len(data) < 2:
            raise ValueError("Insufficient data for housekeeping service")
        
        structure_id = struct.unpack('>H', data[:2])[0]
        
        # Parse parameter values (simplified - would need structure definition in practice)
        parameters = {}
        offset = 2
        param_count = 0
        
        while offset < len(data) - 3:  # Ensure we have at least 4 bytes for a parameter
            try:
                param_id = struct.unpack('>H', data[offset:offset+2])[0]
                param_value = struct.unpack('>I', data[offset+2:offset+6])[0]
                parameters[f"param_{param_id}"] = param_value
                offset += 6
                param_count += 1
                if param_count > 10:  # Safety limit
                    break
            except:
                break
        
        return HousekeepingData(
            service_type=3,
            service_subtype=subtype,
            raw_data=data,
            structure_id=structure_id,
            parameters=parameters
        )
    
    def parse_service_5_event_reporting(self, data: bytes, subtype: int) -> EventData:
        """Parse Service 5 - Event Reporting data"""
        if len(data) < 8:
            raise ValueError("Insufficient data for event reporting service")
        
        event_id = struct.unpack('>H', data[:2])[0]
        severity = EventSeverity(data[2] if data[2] <= 4 else 1)
        
        # Parse timestamp (assuming CUC format)
        timestamp = self.parse_cuc_time(data, 3)
        
        auxiliary_data = data[7:] if len(data) > 7 else None
        
        return EventData(
            service_type=5,
            service_subtype=subtype,
            raw_data=data,
            event_id=event_id,
            severity=severity,
            timestamp=timestamp,
            auxiliary_data=auxiliary_data
        )
    
    def parse_service_6_memory_management(self, data: bytes, subtype: int) -> MemoryData:
        """Parse Service 6 - Memory Management data"""
        if len(data) < 10:
            raise ValueError("Insufficient data for memory management service")
        
        memory_id = struct.unpack('>H', data[:2])[0]
        start_address = struct.unpack('>I', data[2:6])[0]
        length = struct.unpack('>I', data[6:10])[0]
        
        checksum = None
        memory_data = None
        
        if len(data) > 10:
            if subtype in [6, 9]:  # Memory dump or load
                memory_data = data[10:]
            elif subtype == 15:  # Checksum
                checksum = struct.unpack('>I', data[10:14])[0]
        
        return MemoryData(
            service_type=6,
            service_subtype=subtype,
            raw_data=data,
            memory_id=memory_id,
            start_address=start_address,
            length=length,
            checksum=checksum,
            data=memory_data
        )
    
    def parse_service_8_function_management(self, data: bytes, subtype: int) -> FunctionData:
        """Parse Service 8 - Function Management data"""
        if len(data) < 2:
            raise ValueError("Insufficient data for function management service")
        
        function_id = struct.unpack('>H', data[:2])[0]
        
        # Parse arguments (simplified)
        arguments = []
        offset = 2
        while offset < len(data) - 3:
            try:
                arg = struct.unpack('>I', data[offset:offset+4])[0]
                arguments.append(arg)
                offset += 4
                if len(arguments) > 8:  # Safety limit
                    break
            except:
                break
        
        return FunctionData(
            service_type=8,
            service_subtype=subtype,
            raw_data=data,
            function_id=function_id,
            arguments=arguments
        )
    
    def parse_service_4_parameter_statistics(self, data: bytes, subtype: int) -> ParameterStatisticsData:
        """Parse Service 4 - Parameter Statistics data"""
        if len(data) < 8:
            raise ValueError("Insufficient data for parameter statistics service")
        
        parameter_id = struct.unpack('>H', data[:2])[0]
        statistics_type = data[2]
        sampling_interval = struct.unpack('>H', data[3:5])[0]
        value = struct.unpack('>f', data[5:9])[0]
        
        timestamp = None
        min_value = max_value = mean_value = None
        
        offset = 9
        if len(data) >= offset + 4:
            timestamp = self.parse_cuc_time(data, offset)
            offset += 4
            
        if len(data) >= offset + 12:  # min, max, mean values
            min_value = struct.unpack('>f', data[offset:offset+4])[0]
            max_value = struct.unpack('>f', data[offset+4:offset+8])[0]
            mean_value = struct.unpack('>f', data[offset+8:offset+12])[0]
        
        return ParameterStatisticsData(
            service_type=4,
            service_subtype=subtype,
            raw_data=data,
            parameter_id=parameter_id,
            statistics_type=statistics_type,
            sampling_interval=sampling_interval,
            value=value,
            timestamp=timestamp,
            min_value=min_value,
            max_value=max_value,
            mean_value=mean_value
        )
    
    def parse_service_12_monitoring(self, data: bytes, subtype: int) -> MonitoringData:
        """Parse Service 12 - Monitoring and Control data"""
        if len(data) < 6:
            raise ValueError("Insufficient data for monitoring service")
        
        parameter_id = struct.unpack('>H', data[:2])[0]
        check_type = data[2]
        check_status = data[3]
        
        limit_count = data[4] if len(data) > 4 else 0
        offset = 5
        
        limit_values = []
        for i in range(min(limit_count, 4)):  # Max 4 limits
            if offset + 4 <= len(data):
                limit_values.append(struct.unpack('>f', data[offset:offset+4])[0])
                offset += 4
        
        current_value = None
        violation_count = None
        
        if offset + 4 <= len(data):
            current_value = struct.unpack('>f', data[offset:offset+4])[0]
            offset += 4
            
        if offset + 2 <= len(data):
            violation_count = struct.unpack('>H', data[offset:offset+2])[0]
        
        return MonitoringData(
            service_type=12,
            service_subtype=subtype,
            raw_data=data,
            parameter_id=parameter_id,
            check_type=check_type,
            check_status=check_status,
            limit_values=limit_values,
            current_value=current_value,
            violation_count=violation_count
        )
    
    def parse_service_13_large_data(self, data: bytes, subtype: int) -> LargeDataTransferData:
        """Parse Service 13 - Large Data Transfer data"""
        if len(data) < 6:
            raise ValueError("Insufficient data for large data transfer service")
        
        transfer_id = struct.unpack('>H', data[:2])[0]
        part_number = struct.unpack('>H', data[2:4])[0]
        total_parts = struct.unpack('>H', data[4:6])[0]
        data_part = data[6:]
        
        return LargeDataTransferData(
            service_type=13,
            service_subtype=subtype,
            raw_data=data,
            transfer_id=transfer_id,
            part_number=part_number,
            total_parts=total_parts,
            data_part=data_part
        )
    
    def parse_service_14_forwarding(self, data: bytes, subtype: int) -> ForwardingData:
        """Parse Service 14 - Real-time Forwarding data"""
        if len(data) < 2:
            raise ValueError("Insufficient data for forwarding service")
        
        virtual_channel_id = data[0]
        forwarded_data = data[1:]
        
        return ForwardingData(
            service_type=14,
            service_subtype=subtype,
            raw_data=data,
            virtual_channel_id=virtual_channel_id,
            forwarded_data=forwarded_data
        )
    
    def parse_service_15_storage(self, data: bytes, subtype: int) -> StorageData:
        """Parse Service 15 - On-board Storage data"""
        if len(data) < 8:
            raise ValueError("Insufficient data for storage service")
        
        storage_id = struct.unpack('>H', data[:2])[0]
        start_time = self.parse_cuc_time(data, 2)
        end_time = self.parse_cuc_time(data, 6)
        
        stored_data = data[10:] if len(data) > 10 else None
        
        return StorageData(
            service_type=15,
            service_subtype=subtype,
            raw_data=data,
            storage_id=storage_id,
            start_time=start_time,
            end_time=end_time,
            stored_data=stored_data
        )
    
    def parse_service_19_event_action(self, data: bytes, subtype: int) -> EventActionData:
        """Parse Service 19 - Event-Action data"""
        if len(data) < 5:
            raise ValueError("Insufficient data for event-action service")
        
        event_definition_id = struct.unpack('>H', data[:2])[0]
        action_definition_id = struct.unpack('>H', data[2:4])[0]
        enabled = bool(data[4])
        
        return EventActionData(
            service_type=19,
            service_subtype=subtype,
            raw_data=data,
            event_definition_id=event_definition_id,
            action_definition_id=action_definition_id,
            enabled=enabled
        )
    
    def parse_service_20_parameter_management(self, data: bytes, subtype: int) -> ParameterManagementData:
        """Parse Service 20 - Parameter Management data"""
        if len(data) < 7:
            raise ValueError("Insufficient data for parameter management service")
        
        parameter_id = struct.unpack('>H', data[:2])[0]
        validity_flag = bool(data[2])
        
        # Determine parameter type and parse value
        param_type = data[3]
        if param_type == 1:  # Integer
            parameter_value = struct.unpack('>I', data[4:8])[0]
        elif param_type == 2:  # Float
            parameter_value = struct.unpack('>f', data[4:8])[0]
        else:  # String or raw
            parameter_value = data[4:].decode('ascii', errors='ignore')
        
        return ParameterManagementData(
            service_type=20,
            service_subtype=subtype,
            raw_data=data,
            parameter_id=parameter_id,
            parameter_value=parameter_value,
            validity_flag=validity_flag
        )
    
    def parse_service_21_sequencing(self, data: bytes, subtype: int) -> SequencingData:
        """Parse Service 21 - Request Sequencing data"""
        if len(data) < 6:
            raise ValueError("Insufficient data for sequencing service")
        
        sequence_id = struct.unpack('>H', data[:2])[0]
        execution_time = self.parse_cuc_time(data, 2)
        sequence_data = data[6:]
        
        return SequencingData(
            service_type=21,
            service_subtype=subtype,
            raw_data=data,
            sequence_id=sequence_id,
            execution_time=execution_time,
            sequence_data=sequence_data
        )
    
    def parse_service_22_scheduling(self, data: bytes, subtype: int) -> SchedulingData:
        """Parse Service 22 - Position-based Scheduling data"""
        if len(data) < 14:
            raise ValueError("Insufficient data for scheduling service")
        
        schedule_id = struct.unpack('>H', data[:2])[0]
        
        # Parse position data (lat, lon, alt)
        latitude = struct.unpack('>f', data[2:6])[0]
        longitude = struct.unpack('>f', data[6:10])[0]
        altitude = struct.unpack('>f', data[10:14])[0]
        
        position_data = {
            'latitude': latitude,
            'longitude': longitude,
            'altitude': altitude
        }
        
        # Parse execution conditions (simplified)
        execution_conditions = {}
        if len(data) > 14:
            execution_conditions['raw_conditions'] = data[14:]
        
        return SchedulingData(
            service_type=22,
            service_subtype=subtype,
            raw_data=data,
            schedule_id=schedule_id,
            position_data=position_data,
            execution_conditions=execution_conditions
        )
    
    def parse_service_23_file_management(self, data: bytes, subtype: int) -> FileManagementData:
        """Parse Service 23 - File Management data"""
        if len(data) < 4:
            raise ValueError("Insufficient data for file management service")
        
        file_id = struct.unpack('>H', data[:2])[0]
        path_length = data[2]
        
        if len(data) < 3 + path_length:
            raise ValueError("Insufficient data for file path")
        
        file_path = data[3:3+path_length].decode('ascii', errors='ignore')
        offset = 3 + path_length
        
        file_size = None
        file_data = None
        operation_type = None
        file_attributes = {}
        
        if len(data) >= offset + 4:
            file_size = struct.unpack('>I', data[offset:offset+4])[0]
            offset += 4
            
        if len(data) > offset:
            if subtype in [1, 2]:  # Create/Delete
                operation_type = "CREATE" if subtype == 1 else "DELETE"
            elif subtype in [3, 4]:  # Copy/Move
                operation_type = "COPY" if subtype == 3 else "MOVE"
            elif subtype == 5:  # Read
                operation_type = "READ"
                file_data = data[offset:]
            elif subtype == 6:  # Write
                operation_type = "WRITE"
                file_data = data[offset:]
        
        return FileManagementData(
            service_type=23,
            service_subtype=subtype,
            raw_data=data,
            file_id=file_id,
            file_path=file_path,
            file_size=file_size,
            file_data=file_data,
            operation_type=operation_type,
            file_attributes=file_attributes
        )

    def parse_service_9_time_management(self, data: bytes, subtype: int) -> 'TimeData':
        """Parse Service 9 - Time Management data"""
        if len(data) < 5:
            raise ValueError("Insufficient data for time management service")
        
        time_format = data[0]
        
        if time_format == 1:  # CUC format
            time_value = self.parse_cuc_time(data, 1)
        elif time_format == 2:  # CDS format
            time_value = self.parse_cds_time(data, 1)
        else:
            time_value = self.parse_cuc_time(data, 1)
        
        correlation_time = None
        if len(data) > 9:
            correlation_time = self.parse_cuc_time(data, 5)
        
        return TimeData(
            service_type=9,
            service_subtype=subtype,
            raw_data=data,
            time_format=time_format,
            time_value=time_value,
            correlation_time=correlation_time
        )

    def parse_generic_service(self, service_type: int, subtype: int, data: bytes) -> PUSDataField:
        """Parse generic service data for services without specific parsers"""
        return PUSDataField(
            service_type=service_type,
            service_subtype=subtype,
            raw_data=data
        )
    
    def parse_pus_data_field(self, secondary_header: SecondaryHeader, data: bytes) -> Optional[PUSDataField]:
        """Parse PUS service-specific data field"""
        service_type = secondary_header.service_type
        subtype = secondary_header.service_subtype
        
        try:
            if service_type == PUSService.TC_VERIFICATION:
                return self.parse_service_1_verification(data, subtype)
            elif service_type == PUSService.DEVICE_ACCESS:
                return self.parse_service_2_device_access(data, subtype)
            elif service_type == PUSService.HOUSEKEEPING:
                return self.parse_service_3_housekeeping(data, subtype)
            elif service_type == PUSService.EVENT_REPORTING:
                return self.parse_service_5_event_reporting(data, subtype)
            elif service_type == PUSService.MEMORY_MANAGEMENT:
                return self.parse_service_6_memory_management(data, subtype)
            elif service_type == PUSService.FUNCTION_MANAGEMENT:
                return self.parse_service_8_function_management(data, subtype)
            elif service_type == PUSService.TIME_MANAGEMENT:
                return self.parse_service_9_time_management(data, subtype)
            elif service_type == PUSService.PARAMETER_STATISTICS:
                return self.parse_service_4_parameter_statistics(data, subtype)
            elif service_type == PUSService.MONITORING_AND_CONTROL:
                return self.parse_service_12_monitoring(data, subtype)
            elif service_type == PUSService.LARGE_DATA_TRANSFER:
                return self.parse_service_13_large_data(data, subtype)
            elif service_type == PUSService.REAL_TIME_FORWARDING:
                return self.parse_service_14_forwarding(data, subtype)
            elif service_type == PUSService.ON_BOARD_STORAGE:
                return self.parse_service_15_storage(data, subtype)
            elif service_type == PUSService.EVENT_ACTION:
                return self.parse_service_19_event_action(data, subtype)
            elif service_type == PUSService.PARAMETER_MANAGEMENT:
                return self.parse_service_20_parameter_management(data, subtype)
            elif service_type == PUSService.REQUEST_SEQUENCING:
                return self.parse_service_21_sequencing(data, subtype)
            elif service_type == PUSService.POSITION_BASED_SCHEDULING:
                return self.parse_service_22_scheduling(data, subtype)
            elif service_type == PUSService.FILE_MANAGEMENT:
                return self.parse_service_23_file_management(data, subtype)
            else:
                # Generic parser for unknown services
                return self.parse_generic_service(service_type, subtype, data)
                
        except Exception as e:
            self.logger.warning(f"Failed to parse service {service_type} data: {e}")
            return self.parse_generic_service(service_type, subtype, data)

    def parse_primary_header(self, data: bytes) -> 'PacketHeader':
        """Parse ECSS Space Packet Primary Header (6 bytes)"""
        if len(data) < 6:
            raise ValueError("Insufficient data for primary header")
        
        header_data = struct.unpack('>HHH', data[:6])
        
        packet_id = header_data[0]
        packet_version = (packet_id >> 13) & 0x7
        packet_type = PacketType((packet_id >> 12) & 0x1)
        secondary_header_flag = SecondaryHeaderFlag((packet_id >> 11) & 0x1)
        apid = packet_id & 0x7FF
        
        seq_control = header_data[1]
        sequence_flags = (seq_control >> 14) & 0x3
        sequence_count = seq_control & 0x3FFF
        
        data_length = header_data[2]
        
        return PacketHeader(
            packet_version=packet_version,
            packet_type=packet_type,
            secondary_header_flag=secondary_header_flag,
            apid=apid,
            sequence_flags=sequence_flags,
            sequence_count=sequence_count,
            data_length=data_length
        )

    def parse_secondary_header(self, data: bytes, packet_type: PacketType) -> SecondaryHeader:
        """Parse ECSS Space Packet Secondary Header"""
        if len(data) < 1:
            raise ValueError("Insufficient data for secondary header")
        
        if packet_type == PacketType.TELEMETRY:
            # Telemetry packet secondary header (minimum 1 byte)
            if len(data) < 1:
                raise ValueError("Insufficient data for telemetry secondary header")
            
            first_byte = data[0]
            spare = (first_byte >> 4) & 0x1
            pus_version = first_byte & 0xF
            
            if len(data) >= 2:
                service_type = data[1]
                service_subtype = data[2] if len(data) >= 3 else 0
            else:
                service_type = 0
                service_subtype = 0
            
            return SecondaryHeader(
                spare=spare,
                pus_version=pus_version,
                service_type=service_type,
                service_subtype=service_subtype
            )
        
        elif packet_type == PacketType.COMMAND:
            # Command packet secondary header
            if len(data) < 1:
                raise ValueError("Insufficient data for command secondary header")
            
            first_byte = data[0]
            spare = (first_byte >> 4) & 0x1
            pus_version = first_byte & 0xF
            
            service_type = data[1] if len(data) >= 2 else 0
            service_subtype = data[2] if len(data) >= 3 else 0
            
            return SecondaryHeader(
                spare=spare,
                pus_version=pus_version,
                service_type=service_type,
                service_subtype=service_subtype
            )
        
        else:
            raise ValueError(f"Unknown packet type: {packet_type}")
    
    def decommutate_packet(self, data: bytes) -> DecommutatedPacket:
        """Decommutate a complete ECSS space packet"""
        if len(data) < 6:
            raise ValueError("Packet too short - minimum 6 bytes required")
        
        # Parse primary header
        header = self.parse_primary_header(data)
        
        self.logger.info(f"Parsing packet: APID={header.apid}, Type={header.packet_type.name}")
        
        # Calculate expected packet length
        expected_length = header.data_length + 7  # +1 for data length field, +6 for primary header
        
        if len(data) < expected_length:
            self.logger.warning(f"Packet shorter than expected: {len(data)} < {expected_length}")
        
        # Parse secondary header if present
        secondary_header = None
        data_start_idx = 6
        
        if header.secondary_header_flag == SecondaryHeaderFlag.PRESENT:
            try:
                secondary_header = self.parse_secondary_header(data[6:], header.packet_type)
                # Estimate secondary header length (variable, typically 1-4 bytes)
                if header.packet_type == PacketType.TELEMETRY:
                    data_start_idx = 9  # Assume 3-byte secondary header for telemetry
                else:
                    data_start_idx = 9  # Assume 3-byte secondary header for commands
                    
                # Adjust if we don't have enough data
                data_start_idx = min(data_start_idx, len(data))
                
            except Exception as e:
                self.logger.warning(f"Failed to parse secondary header: {e}")
                data_start_idx = 7  # Skip first byte and continue
        
        # Extract data field
        data_end_idx = 6 + header.data_length + 1
        data_field = data[data_start_idx:data_end_idx]
        
        # Parse PUS data field if secondary header is present
        parsed_data = None
        if secondary_header and header.packet_type == PacketType.TELEMETRY:
            parsed_data = self.parse_pus_data_field(secondary_header, data_field)
        
        # Extract packet error control if present (typically last 2 bytes)
        packet_error_control = None
        if len(data) >= data_end_idx + 2:
            packet_error_control = data[-2:]
        
        return DecommutatedPacket(
            header=header,
            secondary_header=secondary_header,
            data_field=data_field,
            parsed_data=parsed_data,
            packet_error_control=packet_error_control,
            raw_data=data
        )
    
    def build_command_packet(self, cmd_request: CommandRequest) -> bytes:
        """Build a complete ECSS command packet"""
        # Build secondary header (3 bytes minimum for PUS)
        secondary_header = bytes([
            0x10,  # Spare=0, PUS Version=0
            cmd_request.service_type,
            cmd_request.service_subtype
        ])
        
        # Calculate total data length (secondary header + data field)
        total_data_length = len(secondary_header) + len(cmd_request.data)
        
        # Build primary header
        packet_id = (0 << 13) | (1 << 12) | (1 << 11) | (cmd_request.apid & 0x7FF)  # Ver=0, Type=TC, SecHdr=1
        seq_control = (0b01 << 14) | (cmd_request.sequence_count & 0x3FFF)  # Flags=01, SeqCount
        data_length = total_data_length - 1  # Data length field excludes itself
        
        primary_header = struct.pack('>HHH', packet_id, seq_control, data_length)
        
        # Combine all parts
        packet = primary_header + secondary_header + cmd_request.data
        
        # Add packet error control (simple checksum for demonstration)
        checksum = sum(packet) & 0xFFFF
        packet += struct.pack('>H', checksum)
        
        return packet
    
    def create_command_builder(self, apid: int) -> CommandBuilder:
        """Create a command builder for a specific APID"""
        return CommandBuilder(apid=apid)
    
    # Command building methods for each service
    def build_service_1_verify_command(self, builder: CommandBuilder, request_id: int, ack_flags: int = 0b1111) -> bytes:
        """Build Service 1 - TC Verification enable/disable command"""
        data = struct.pack('>HB', request_id, ack_flags)
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=1,
            service_subtype=1,  # Enable verification
            data=data,
            sequence_count=builder.increment_sequence(),
            ack_flags=ack_flags
        )
        return self.build_command_packet(cmd)
    
    def build_service_2_device_command(self, builder: CommandBuilder, device_id: int, device_data: bytes) -> bytes:
        """Build Service 2 - Device Access command"""
        data = struct.pack('>H', device_id) + device_data
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=2,
            service_subtype=1,  # Device command
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_3_housekeeping_command(self, builder: CommandBuilder, structure_id: int, 
                                           collection_interval: int = 0, subtype: int = 1) -> bytes:
        """Build Service 3 - Housekeeping command"""
        data = struct.pack('>HI', structure_id, collection_interval)
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=3,
            service_subtype=subtype,  # 1=Enable, 2=Disable, 5=Report
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_5_event_command(self, builder: CommandBuilder, event_id: int, 
                                    enable: bool = True) -> bytes:
        """Build Service 5 - Event Reporting enable/disable command"""
        subtype = 5 if enable else 6  # Enable/Disable event reporting
        data = struct.pack('>H', event_id)
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=5,
            service_subtype=subtype,
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_6_memory_command(self, builder: CommandBuilder, memory_id: int, 
                                     start_address: int, length: int, 
                                     data_to_write: bytes = None, subtype: int = 5) -> bytes:
        """Build Service 6 - Memory Management command"""
        data = struct.pack('>HII', memory_id, start_address, length)
        if data_to_write and subtype == 2:  # Memory load
            data += data_to_write
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=6,
            service_subtype=subtype,  # 2=Load, 5=Dump, 9=Check
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_8_function_command(self, builder: CommandBuilder, function_id: int, 
                                       arguments: List[int] = None) -> bytes:
        """Build Service 8 - Function Management command"""
        data = struct.pack('>H', function_id)
        if arguments:
            for arg in arguments:
                data += struct.pack('>I', arg)
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=8,
            service_subtype=1,  # Perform function
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_9_time_command(self, builder: CommandBuilder, time_value: datetime, 
                                   time_format: int = 1) -> bytes:
        """Build Service 9 - Time Management command"""
        # Convert datetime to seconds since epoch
        time_seconds = int((time_value - self.time_epoch).total_seconds())
        
        data = struct.pack('>BI', time_format, time_seconds)
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=9,
            service_subtype=1,  # Set time
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_11_time_schedule_command(self, builder: CommandBuilder, 
                                             execution_time: datetime, 
                                             command_data: bytes) -> bytes:
        """Build Service 11 - Time-based Scheduling command"""
        time_seconds = int((execution_time - self.time_epoch).total_seconds())
        data = struct.pack('>I', time_seconds) + command_data
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=11,
            service_subtype=4,  # Insert time-tagged command
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_12_monitoring_command(self, builder: CommandBuilder, parameter_id: int,
                                          check_type: int, limit_values: List[float],
                                          enable: bool = True) -> bytes:
        """Build Service 12 - Monitoring and Control command"""
        subtype = 1 if enable else 2  # Enable/Disable monitoring
        data = struct.pack('>HBB', parameter_id, check_type, len(limit_values))
        
        for limit in limit_values:
            data += struct.pack('>f', limit)
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=12,
            service_subtype=subtype,
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_17_test_command(self, builder: CommandBuilder, test_id: int) -> bytes:
        """Build Service 17 - Test command"""
        data = struct.pack('>H', test_id)
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=17,
            service_subtype=1,  # Perform connection test
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_19_event_action_command(self, builder: CommandBuilder, 
                                            event_definition_id: int,
                                            action_definition_id: int,
                                            enable: bool = True) -> bytes:
        """Build Service 19 - Event-Action command"""
        subtype = 1 if enable else 2  # Add/Delete event-action definition
        data = struct.pack('>HHB', event_definition_id, action_definition_id, int(enable))
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=19,
            service_subtype=subtype,
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_20_parameter_command(self, builder: CommandBuilder, parameter_id: int,
                                         parameter_value: Union[int, float], 
                                         param_type: int = 1) -> bytes:
        """Build Service 20 - Parameter Management command"""
        data = struct.pack('>HB', parameter_id, param_type)
        
        if param_type == 1:  # Integer
            data += struct.pack('>I', int(parameter_value))
        elif param_type == 2:  # Float
            data += struct.pack('>f', float(parameter_value))
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=20,
            service_subtype=1,  # Set parameter
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def build_service_23_file_command(self, builder: CommandBuilder, file_id: int,
                                    file_path: str, operation: str = "CREATE",
                                    file_data: bytes = None) -> bytes:
        """Build Service 23 - File Management command"""
        operation_map = {
            "CREATE": 1, "DELETE": 2, "COPY": 3, "MOVE": 4, "READ": 5, "WRITE": 6
        }
        subtype = operation_map.get(operation.upper(), 1)
        
        # Encode file path
        path_bytes = file_path.encode('ascii')
        data = struct.pack('>HB', file_id, len(path_bytes)) + path_bytes
        
        if file_data and operation.upper() == "WRITE":
            data += struct.pack('>I', len(file_data)) + file_data
        
        cmd = CommandRequest(
            apid=builder.apid,
            service_type=23,
            service_subtype=subtype,
            data=data,
            sequence_count=builder.increment_sequence()
        )
        return self.build_command_packet(cmd)
    
    def decommutate_multiple(self, data: bytes) -> List[DecommutatedPacket]:
        """Decommutate multiple concatenated ECSS packets"""
        packets = []
        offset = 0
        
        while offset < len(data) - 6:  # Need at least 6 bytes for header
            try:
                # Parse header to get packet length
                header = self.parse_primary_header(data[offset:])
                packet_length = header.data_length + 7
                
                if offset + packet_length > len(data):
                    self.logger.warning(f"Incomplete packet at offset {offset}")
                    break
                
                # Extract and decommutate packet
                packet_data = data[offset:offset + packet_length]
                packet = self.decommutate_packet(packet_data)
                packets.append(packet)
                
                offset += packet_length
                
            except Exception as e:
                self.logger.error(f"Error parsing packet at offset {offset}: {e}")
                offset += 1  # Skip one byte and try again
        
        return packets
    
    def to_dict(self, packet: DecommutatedPacket) -> Dict[str, Any]:
        """Convert decommutated packet to dictionary for JSON serialization"""
        result = {
            "header": {
                "packet_version": packet.header.packet_version,
                "packet_type": packet.header.packet_type.name,
                "secondary_header_flag": packet.header.secondary_header_flag.name,
                "apid": packet.header.apid,
                "sequence_flags": packet.header.sequence_flags,
                "sequence_count": packet.header.sequence_count,
                "data_length": packet.header.data_length
            },
            "data_field_hex": packet.data_field.hex(),
            "data_field_length": len(packet.data_field)
        }
        
        if packet.secondary_header:
            result["secondary_header"] = {
                "spare": packet.secondary_header.spare,
                "pus_version": packet.secondary_header.pus_version,
                "service_type": packet.secondary_header.service_type,
                "service_subtype": packet.secondary_header.service_subtype
            }
            
            # Add service name for readability
            try:
                service_name = PUSService(packet.secondary_header.service_type).name
                result["secondary_header"]["service_name"] = service_name
            except ValueError:
                result["secondary_header"]["service_name"] = "UNKNOWN"
        
        if packet.parsed_data:
            result["parsed_data"] = self._serialize_parsed_data(packet.parsed_data)
        
        if packet.packet_error_control:
            result["packet_error_control"] = packet.packet_error_control.hex()
        
        return result
    
    def _serialize_parsed_data(self, parsed_data: PUSDataField) -> Dict[str, Any]:
        """Serialize parsed PUS data to dictionary"""
        result = {
            "service_type": parsed_data.service_type,
            "service_subtype": parsed_data.service_subtype,
            "data_type": type(parsed_data).__name__
        }
        
        # Service-specific serialization
        if isinstance(parsed_data, VerificationData):
            result.update({
                "request_id": parsed_data.request_id,
                "error_code": parsed_data.error_code,
                "error_data": parsed_data.error_data.hex() if parsed_data.error_data else None
            })
        elif isinstance(parsed_data, DeviceAccessData):
            result.update({
                "device_id": parsed_data.device_id,
                "raw_device_data": parsed_data.raw_device_data.hex()
            })
        elif isinstance(parsed_data, HousekeepingData):
            result.update({
                "structure_id": parsed_data.structure_id,
                "parameters": parsed_data.parameters
            })
        elif isinstance(parsed_data, EventData):
            result.update({
                "event_id": parsed_data.event_id,
                "severity": parsed_data.severity.name,
                "timestamp": parsed_data.timestamp.isoformat(),
                "auxiliary_data": parsed_data.auxiliary_data.hex() if parsed_data.auxiliary_data else None
            })
        elif isinstance(parsed_data, MemoryData):
            result.update({
                "memory_id": parsed_data.memory_id,
                "start_address": f"0x{parsed_data.start_address:08X}",
                "length": parsed_data.length,
                "checksum": f"0x{parsed_data.checksum:08X}" if parsed_data.checksum else None,
                "data": parsed_data.data.hex() if parsed_data.data else None
            })
        elif isinstance(parsed_data, FunctionData):
            result.update({
                "function_id": parsed_data.function_id,
                "arguments": parsed_data.arguments
            })
        elif isinstance(parsed_data, TimeData):
            result.update({
                "time_format": parsed_data.time_format,
                "time_value": parsed_data.time_value.isoformat(),
                "correlation_time": parsed_data.correlation_time.isoformat() if parsed_data.correlation_time else None
            })
        elif isinstance(parsed_data, ParameterStatisticsData):
            result.update({
                "parameter_id": parsed_data.parameter_id,
                "statistics_type": parsed_data.statistics_type,
                "sampling_interval": parsed_data.sampling_interval,
                "value": parsed_data.value,
                "timestamp": parsed_data.timestamp.isoformat() if parsed_data.timestamp else None,
                "min_value": parsed_data.min_value,
                "max_value": parsed_data.max_value,
                "mean_value": parsed_data.mean_value
            })
        elif isinstance(parsed_data, MonitoringData):
            result.update({
                "parameter_id": parsed_data.parameter_id,
                "check_type": parsed_data.check_type,
                "check_status": parsed_data.check_status,
                "limit_values": parsed_data.limit_values,
                "current_value": parsed_data.current_value,
                "violation_count": parsed_data.violation_count
            })
        elif isinstance(parsed_data, LargeDataTransferData):
            result.update({
                "transfer_id": parsed_data.transfer_id,
                "part_number": parsed_data.part_number,
                "total_parts": parsed_data.total_parts,
                "data_part": parsed_data.data_part.hex()
            })
        elif isinstance(parsed_data, ForwardingData):
            result.update({
                "virtual_channel_id": parsed_data.virtual_channel_id,
                "forwarded_data": parsed_data.forwarded_data.hex()
            })
        elif isinstance(parsed_data, StorageData):
            result.update({
                "storage_id": parsed_data.storage_id,
                "start_time": parsed_data.start_time.isoformat(),
                "end_time": parsed_data.end_time.isoformat(),
                "stored_data": parsed_data.stored_data.hex() if parsed_data.stored_data else None
            })
        elif isinstance(parsed_data, EventActionData):
            result.update({
                "event_definition_id": parsed_data.event_definition_id,
                "action_definition_id": parsed_data.action_definition_id,
                "enabled": parsed_data.enabled
            })
        elif isinstance(parsed_data, ParameterManagementData):
            result.update({
                "parameter_id": parsed_data.parameter_id,
                "parameter_value": parsed_data.parameter_value,
                "validity_flag": parsed_data.validity_flag
            })
        elif isinstance(parsed_data, SequencingData):
            result.update({
                "sequence_id": parsed_data.sequence_id,
                "execution_time": parsed_data.execution_time.isoformat(),
                "sequence_data": parsed_data.sequence_data.hex()
            })
        elif isinstance(parsed_data, SchedulingData):
            result.update({
                "schedule_id": parsed_data.schedule_id,
                "position_data": parsed_data.position_data,
                "execution_conditions": parsed_data.execution_conditions
            })
        elif isinstance(parsed_data, FileManagementData):
            result.update({
                "file_id": parsed_data.file_id,
                "file_path": parsed_data.file_path,
                "file_size": parsed_data.file_size,
                "operation_type": parsed_data.operation_type,
                "file_data": parsed_data.file_data.hex() if parsed_data.file_data else None,
                "file_attributes": parsed_data.file_attributes
            })
        else:
            # Generic data
            result["raw_data"] = parsed_data.raw_data.hex()
        
        return result

def main():
    """Example usage and testing with multiple PUS services and command building"""
    
    # Create decommutator
    decom = ECSSDecommutator(enable_logging=True)
    
    print("=== ECSS PUS Service Decommutator & Command Builder Examples ===\n")
    
    # ==================== TELEMETRY EXAMPLES ====================
    
    # Example 1: Service 4 - Parameter Statistics
    statistics_packet = bytes([
        # Primary Header (6 bytes)
        0x08, 0x05,  # Packet ID: Version=0, Type=TM, Sec Hdr=1, APID=5
        0x40, 0x04,  # Sequence Control: Flags=01, Count=4
        0x00, 0x14,  # Data Length: 20 bytes
        
        # Secondary Header (3 bytes) 
        0x10,        # Spare=0, PUS Version=0
        0x04,        # Service Type 4 (Parameter Statistics)
        0x01,        # Service Subtype 1 (Statistics Report)
        
        # Data Field (18 bytes)
        0x00, 0x42,  # Parameter ID = 66
        0x01,        # Statistics Type = 1 (Average)
        0x00, 0x3C,  # Sampling Interval = 60 seconds
        0x42, 0x48, 0x00, 0x00,  # Value = 50.0 (float)
        0x00, 0x00, 0x12, 0x34,  # Timestamp
        0x41, 0xC8, 0x00, 0x00,  # Min = 25.0
        0x42, 0x96, 0x00, 0x00,  # Max = 75.0
        0x42, 0x48, 0x00, 0x00   # Mean = 50.0
    ])
    
    # Example 2: Service 12 - Monitoring and Control
    monitoring_packet = bytes([
        # Primary Header (6 bytes)
        0x08, 0x06,  # Packet ID: Version=0, Type=TM, Sec Hdr=1, APID=6
        0x40, 0x05,  # Sequence Control: Flags=01, Count=5
        0x00, 0x10,  # Data Length: 16 bytes
        
        # Secondary Header (3 bytes) 
        0x10,        # Spare=0, PUS Version=0
        0x0C,        # Service Type 12 (Monitoring)
        0x01,        # Service Subtype 1 (Out of Limit Report)
        
        # Data Field (14 bytes)
        0x00, 0x33,  # Parameter ID = 51
        0x02,        # Check Type = 2 (Limit Check)
        0x01,        # Check Status = 1 (Violation)
        0x02,        # Limit Count = 2
        0x42, 0x20, 0x00, 0x00,  # Lower Limit = 40.0
        0x42, 0x70, 0x00, 0x00,  # Upper Limit = 60.0
        0x42, 0x8C, 0x00, 0x00   # Current Value = 70.0 (violation!)
    ])
    
    # Example 3: Service 23 - File Management
    file_packet = bytes([
        # Primary Header (6 bytes)
        0x08, 0x07,  # Packet ID: Version=0, Type=TM, Sec Hdr=1, APID=7
        0x40, 0x06,  # Sequence Control: Flags=01, Count=6
        0x00, 0x15,  # Data Length: 21 bytes
        
        # Secondary Header (3 bytes) 
        0x10,        # Spare=0, PUS Version=0
        0x17,        # Service Type 23 (File Management)
        0x02,        # Service Subtype 2 (File Info Report)
        
        # Data Field (19 bytes)
        0x00, 0x01,  # File ID = 1
        0x0C,        # Path Length = 12
        # File path: "/tmp/data.bin"
        0x2F, 0x74, 0x6D, 0x70, 0x2F, 0x64, 0x61, 0x74, 0x61, 0x2E, 0x62, 0x69, 0x6E,
        0x00, 0x00, 0x04, 0x00   # File Size = 1024 bytes
    ])
    
    telemetry_examples = [
        ("Service 4 - Parameter Statistics", statistics_packet),
        ("Service 12 - Monitoring and Control", monitoring_packet),
        ("Service 23 - File Management", file_packet)
    ]
    
    for name, packet_data in telemetry_examples:
        try:
            print(f"=== {name} ===")
            packet = decom.decommutate_packet(packet_data)
            
            print(f"APID: {packet.header.apid}")
            print(f"Sequence Count: {packet.header.sequence_count}")
            
            if packet.secondary_header:
                service_name = "UNKNOWN"
                try:
                    service_name = PUSService(packet.secondary_header.service_type).name
                except ValueError:
                    pass
                
                print(f"Service: {packet.secondary_header.service_type} ({service_name})")
                print(f"Subtype: {packet.secondary_header.service_subtype}")
            
            if packet.parsed_data:
                print(f"Parsed Data Type: {type(packet.parsed_data).__name__}")
                
                if isinstance(packet.parsed_data, ParameterStatisticsData):
                    print(f"Parameter ID: {packet.parsed_data.parameter_id}")
                    print(f"Statistics Type: {packet.parsed_data.statistics_type}")
                    print(f"Current Value: {packet.parsed_data.value}")
                    print(f"Min/Max/Mean: {packet.parsed_data.min_value}/{packet.parsed_data.max_value}/{packet.parsed_data.mean_value}")
                elif isinstance(packet.parsed_data, MonitoringData):
                    print(f"Parameter ID: {packet.parsed_data.parameter_id}")
                    print(f"Check Status: {'VIOLATION' if packet.parsed_data.check_status else 'NORMAL'}")
                    print(f"Current Value: {packet.parsed_data.current_value}")
                    print(f"Limits: {packet.parsed_data.limit_values}")
                elif isinstance(packet.parsed_data, FileManagementData):
                    print(f"File ID: {packet.parsed_data.file_id}")
                    print(f"File Path: {packet.parsed_data.file_path}")
                    print(f"File Size: {packet.parsed_data.file_size} bytes")
                    print(f"Operation: {packet.parsed_data.operation_type}")
            
            print()
            
        except Exception as e:
            print(f"Error processing {name}: {e}\n")
    
    # ==================== COMMAND BUILDING EXAMPLES ====================
    
    print("=== Command Building Examples ===\n")
    
    # Create command builder for APID 100
    cmd_builder = decom.create_command_builder(apid=100)
    
    try:
        # Example 1: Housekeeping Collection Command
        print("1. Service 3 - Enable Housekeeping Collection")
        hk_cmd = decom.build_service_3_housekeeping_command(
            cmd_builder, 
            structure_id=1, 
            collection_interval=30,
            subtype=1  # Enable
        )
        print(f"Command packet: {hk_cmd.hex()}")
        
        # Verify by decommutating the command we just built
        decom_cmd = decom.decommutate_packet(hk_cmd[:-2])  # Remove checksum for decommutation
        print(f"Verified - Service: {decom_cmd.secondary_header.service_type}, Subtype: {decom_cmd.secondary_header.service_subtype}")
        print()
        
        # Example 2: Memory Dump Command
        print("2. Service 6 - Memory Dump Command")
        mem_cmd = decom.build_service_6_memory_command(
            cmd_builder,
            memory_id=1,
            start_address=0x10000000,
            length=256,
            subtype=5  # Dump memory
        )
        print(f"Command packet: {mem_cmd.hex()}")
        print()
        
        # Example 3: Time Set Command
        print("3. Service 9 - Set Spacecraft Time")
        from datetime import datetime, timedelta
        target_time = datetime.now() + timedelta(hours=1)  # Set time 1 hour in future
        time_cmd = decom.build_service_9_time_command(cmd_builder, target_time)
        print(f"Command packet: {time_cmd.hex()}")
        print(f"Target time: {target_time}")
        print()
        
        # Example 4: Parameter Set Command
        print("4. Service 20 - Set Parameter Value")
        param_cmd = decom.build_service_20_parameter_command(
            cmd_builder,
            parameter_id=42,
            parameter_value=123.45,
            param_type=2  # Float
        )
        print(f"Command packet: {param_cmd.hex()}")
        print()
        
        # Example 5: File Operation Command
        print("5. Service 23 - Create File")
        file_cmd = decom.build_service_23_file_command(
            cmd_builder,
            file_id=10,
            file_path="/var/log/mission.log",
            operation="CREATE"
        )
        print(f"Command packet: {file_cmd.hex()}")
        print()
        
        # Example 6: Event-Action Command
        print("6. Service 19 - Enable Event-Action")
        event_cmd = decom.build_service_19_event_action_command(
            cmd_builder,
            event_definition_id=255,
            action_definition_id=100,
            enable=True
        )
        print(f"Command packet: {event_cmd.hex()}")
        print()
        
        # Example 7: Function Call Command
        print("7. Service 8 - Execute Function")
        func_cmd = decom.build_service_8_function_command(
            cmd_builder,
            function_id=42,
            arguments=[1000, 2000, 3000]
        )
        print(f"Command packet: {func_cmd.hex()}")
        print()
        
    except Exception as e:
        print(f"Command building error: {e}")
    
    # ==================== JSON EXPORT EXAMPLE ====================
    
    print("=== JSON Export Example ===")
    try:
        packet = decom.decommutate_packet(monitoring_packet)
        packet_dict = decom.to_dict(packet)
        print(json.dumps(packet_dict, indent=2))
    except Exception as e:
        print(f"JSON export error: {e}")

if __name__ == "__main__":
    main()
