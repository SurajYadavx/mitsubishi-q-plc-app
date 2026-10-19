"""
core/adapter_registry.py
────────────────────────
Adapter Registry and Factory for the Universal PLC Platform.
Maps protocol identifiers to adapter implementations and manages adapter lifecycles.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Type

from core.adapter_interface import IPlcProtocolAdapter, ProtocolMetadata
from adapters.mitsubishi.mc_protocol import MitsubishiMcProtocolAdapter
from core.simulated_adapter import SimulatedPlcAdapter

logger = logging.getLogger(__name__)


class UnknownAdapterError(Exception):
    """Raised when an unconfigured or unknown protocol adapter identifier is requested."""
    pass


class AdapterRegistry:
    """
    Central registry of protocol adapters.
    Maintains a catalog of available industrial drivers and instantiates them from configuration.
    """

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[Dict[str, Any]], IPlcProtocolAdapter]] = {}
        self._metadata_catalog: Dict[str, ProtocolMetadata] = {}

        # Register standard built-in adapters
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register default protocol adapters."""
        # 1. Mitsubishi MC Protocol 3E Binary
        def mc_factory(conn_params: Dict[str, Any]) -> MitsubishiMcProtocolAdapter:
            return MitsubishiMcProtocolAdapter(
                host=str(conn_params.get("host", "192.168.3.39")),
                port=int(conn_params.get("port", 5010)),
                timeout=float(conn_params.get("timeout_sec", 3.0)),
                frame_type=str(conn_params.get("frame_type", "3E")),
            )

        dummy_mc = MitsubishiMcProtocolAdapter()
        self.register("mitsubishi_mc_3e", mc_factory, dummy_mc.metadata)

        # 2. Deterministic Simulated Adapter (for testing & offline execution)
        def sim_factory(conn_params: Dict[str, Any]) -> SimulatedPlcAdapter:
            return SimulatedPlcAdapter(
                name=str(conn_params.get("name", "Simulated PLC")),
                simulate_fault=bool(conn_params.get("simulate_fault", False)),
            )

        dummy_sim = SimulatedPlcAdapter()
        self.register("simulated", sim_factory, dummy_sim.metadata)

        # 3. Modbus TCP Protocol Adapter (Read-Only)
        def modbus_factory(conn_params: Dict[str, Any]) -> IPlcProtocolAdapter:
            from adapters.modbus.modbus_tcp import ModbusTcpAdapter
            return ModbusTcpAdapter(
                host=str(conn_params.get("host", "127.0.0.1")),
                port=int(conn_params.get("port", 502)),
                unit_id=int(conn_params.get("unit_id", 1)),
                timeout=float(conn_params.get("timeout_sec", 3.0)),
                word_order=str(conn_params.get("word_order", "big_endian")),
            )

        from adapters.modbus.modbus_tcp import ModbusTcpAdapter
        dummy_modbus = ModbusTcpAdapter()
        self.register("modbus_tcp", modbus_factory, dummy_modbus.metadata)

    def register(
        self,
        adapter_id: str,
        factory: Callable[[Dict[str, Any]], IPlcProtocolAdapter],
        metadata: ProtocolMetadata,
    ) -> None:
        """Register a new protocol adapter factory and its capability metadata."""
        self._factories[adapter_id] = factory
        self._metadata_catalog[adapter_id] = metadata
        logger.debug("Registered adapter '%s' (%s)", adapter_id, metadata.display_name)

    def is_registered(self, adapter_id: str) -> bool:
        """Check if an adapter identifier is known to the registry."""
        return adapter_id in self._factories

    def get_metadata(self, adapter_id: str) -> Optional[ProtocolMetadata]:
        """Retrieve protocol metadata for an adapter ID."""
        return self._metadata_catalog.get(adapter_id)

    def list_adapters(self) -> Dict[str, ProtocolMetadata]:
        """Return all registered protocol adapters and their metadata."""
        return dict(self._metadata_catalog)

    def create_adapter(self, adapter_id: str, connection_params: Dict[str, Any]) -> IPlcProtocolAdapter:
        """
        Instantiate an adapter with the specified connection parameters.
        Raises UnknownAdapterError if the adapter_id is not registered.
        """
        if not self.is_registered(adapter_id):
            known = ", ".join(self._factories.keys())
            raise UnknownAdapterError(
                f"Unknown protocol adapter '{adapter_id}'. Registered adapters are: [{known}]"
            )
        factory = self._factories[adapter_id]
        return factory(connection_params)


# Default global singleton registry instance
default_adapter_registry = AdapterRegistry()
