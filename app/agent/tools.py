from fastapi import HTTPException

from app.agent.registry import TOOLS


def execute(services, context, name, arguments):
    tool = TOOLS.get(name)
    if tool is None or name not in context.allowed_tools:
        raise HTTPException(403, "TOOL_NOT_ALLOWED")
    args = tool.schema.model_validate(arguments)
    return services.invoke(context, name, args)
