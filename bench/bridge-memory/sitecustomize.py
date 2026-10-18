"""Opt-in, content-free side observation; never changes handle's outcome."""
import ctypes
import json
import os
import tracemalloc
from pathlib import Path

try:
    if os.environ.get('BRIDGE_PROBE_OUTPUT'):

        from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
        tracemalloc.start(1)
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in ('peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','commit','peak_commit','private')]
        original = DshRpcV2Server.handle
        count = 0
        def observed(self, request):
            global count
            try:
                return original(self, request)
            finally:
                try:
                    count += 1
                    if str(request.get('request_id','')).startswith('sample-'):
                        c = Counters(); c.cb = ctypes.sizeof(c)
                        if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(c), c.cb):
                            raise OSError('process counters unavailable')
                        current, peak = tracemalloc.get_traced_memory()
                        top = [{'file':Path(s.traceback[0].filename).name,'line':s.traceback[0].lineno,'bytes':s.size,'count':s.count} for s in tracemalloc.take_snapshot().statistics('lineno')[:8]]
                        record = dict(stage=request['request_id'], requests=count, commit=c.private, ws=c.ws, peak_ws=c.peak_ws, heap=current, peak_heap=peak, top=top)
                        with open(os.environ['BRIDGE_PROBE_OUTPUT'],'a',encoding='utf-8') as f:
                            f.write(json.dumps(record)+'\n')
                except BaseException:
                    pass
        DshRpcV2Server.handle = observed
except BaseException:
    pass
