"""Per-process run budgets. Every HTTP retry counts as a request."""
from __future__ import annotations
import time
import math
from collections import defaultdict


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self,config=None):
        cfg=config or {}
        self.max_requests=int(cfg.get('max_requests',250))
        self.max_bytes=int(cfg.get('max_bytes',250_000_000))
        self.max_runtime_seconds=float(cfg.get('max_runtime_seconds',900))
        self.max_cost_usd=float(cfg.get('max_cost_usd',0))
        self.request_costs=cfg.get('request_cost_usd',{})
        if self.max_requests<0 or self.max_bytes<0 or self.max_runtime_seconds<=0 or not math.isfinite(self.max_runtime_seconds):
            raise ValueError('Budget limits must be nonnegative; runtime must be positive and finite')
        if self.max_cost_usd<0 or not math.isfinite(self.max_cost_usd) or any(float(v)<0 or not math.isfinite(float(v)) for v in self.request_costs.values()):
            raise ValueError('Estimated request costs and cost limit must be finite and nonnegative')
        self.requests=0;self.bytes=0;self.estimated_cost_usd=0.0
        self.started=time.monotonic();self.by_source=defaultdict(lambda:{'requests':0,'bytes':0,'estimated_cost_usd':0.0})

    def check_time(self):
        if time.monotonic()-self.started>=self.max_runtime_seconds:raise BudgetExceeded('Runtime budget exhausted; pending work retained')

    def before_request(self,source):
        self.check_time()
        cost=float(self.request_costs.get(source,0))
        if self.requests>=self.max_requests:raise BudgetExceeded('Request budget exhausted; pending work retained')
        if self.bytes>=self.max_bytes:raise BudgetExceeded('Data budget exhausted; pending work retained')
        if self.estimated_cost_usd+cost>self.max_cost_usd+1e-12:raise BudgetExceeded('Estimated request cost budget exhausted')
        self.requests+=1;self.estimated_cost_usd+=cost
        self.by_source[source]['requests']+=1;self.by_source[source]['estimated_cost_usd']+=cost

    def consume_bytes(self,source,count):
        self.bytes+=count;self.by_source[source]['bytes']+=count
        if self.bytes>self.max_bytes:raise BudgetExceeded('Data budget exhausted while receiving response; partial file not accepted')
        self.check_time()

    def timeout(self,default):
        self.check_time()
        return max(.1,min(default,self.max_runtime_seconds-(time.monotonic()-self.started)))

    def snapshot(self):
        return {'requests':self.requests,'bytes':self.bytes,'elapsed_seconds':round(time.monotonic()-self.started,3),
                'estimated_cost_usd':self.estimated_cost_usd,'limits':{'requests':self.max_requests,'bytes':self.max_bytes,
                'runtime_seconds':self.max_runtime_seconds,'estimated_cost_usd':self.max_cost_usd},'by_source':dict(self.by_source),
                'cost_caveat':'Estimates from explicit per-request configuration; subscription/license fees are external dependencies.'}
