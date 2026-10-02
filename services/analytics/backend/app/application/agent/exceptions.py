class AgentError(Exception):
    """DataPilot-AI 智能体故障的基础异常。"""


class IntentClassificationError(AgentError):
    """智能体无法识别用户意图时抛出。"""


class AgentRoutingError(AgentError):
    """智能体无法路由已分类请求时抛出。"""


class AgentExecutionError(AgentError):
    """智能体专业节点执行失败时抛出。"""
