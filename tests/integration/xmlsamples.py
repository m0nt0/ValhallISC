"""Hand-written IRIS XML export snippets used by contract and e2e tests."""

HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n<Export generator="IRIS" version="26">\n'


def class_xml(name: str, body: str = 'quit "v1"', description: str = "Contract test class") -> bytes:
    return (
        HEADER
        + f"""<Class name="{name}">
<Description>{description}</Description>
<Method name="Value">
<ClassMethod>1</ClassMethod>
<ReturnType>%String</ReturnType>
<Implementation><![CDATA[
    {body}
]]></Implementation>
</Method>
</Class>
</Export>
"""
    ).encode("utf-8")


def routine_xml(name: str, line: str = "quit") -> bytes:
    return (
        HEADER
        + f"""<Routine name="{name}" type="MAC" languagemode="0"><![CDATA[
{name} ; irisfs contract routine
    {line}
]]></Routine>
</Export>
"""
    ).encode("utf-8")
