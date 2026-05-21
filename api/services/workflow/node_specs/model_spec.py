from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from types import NoneType
from typing import Any, Callable, Literal, get_args, get_origin

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo, PydanticUndefined

from api.services.workflow.node_specs._base import (
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
)

_SPEC_FIELD_META_KEY = "__dograh_spec_field__"
_UNSET = object()


@dataclass(frozen=True)
class NodeSpecMetadata:
    name: str
    display_name: str
    description: str
    category: NodeCategory
    icon: str
    llm_hint: str | None = None
    version: str = "1.0.0"
    examples: tuple[NodeExample, ...] = ()
    graph_constraints: GraphConstraints | None = None
    property_order: tuple[str, ...] = ()
    field_overrides: dict[str, dict[str, Any]] = dataclass_field(default_factory=dict)


def spec_field(
    *field_args: Any,
    ui_type: PropertyType | str | None = None,
    display_name: str | None = None,
    llm_hint: str | None = None,
    required: bool | None = None,
    spec_default: Any = _UNSET,
    placeholder: str | None = None,
    display_options: DisplayOptions | None = None,
    options: list[PropertyOption] | None = None,
    editor: str | None = None,
    extra: dict[str, Any] | None = None,
    spec_exclude: bool = False,
    min_value: float | None = None,
    max_value: float | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    pattern: str | None = None,
    **field_kwargs: Any,
):
    json_schema_extra = dict(field_kwargs.pop("json_schema_extra", {}) or {})
    json_schema_extra[_SPEC_FIELD_META_KEY] = {
        "ui_type": ui_type.value if isinstance(ui_type, PropertyType) else ui_type,
        "display_name": display_name,
        "llm_hint": llm_hint,
        "required": required,
        "placeholder": placeholder,
        "display_options": display_options,
        "options": options,
        "editor": editor,
        "extra": extra or {},
        "spec_exclude": spec_exclude,
        "min_value": min_value,
        "max_value": max_value,
        "min_length": min_length,
        "max_length": max_length,
        "pattern": pattern,
    }
    if spec_default is not _UNSET:
        json_schema_extra[_SPEC_FIELD_META_KEY]["spec_default"] = spec_default
    return Field(*field_args, json_schema_extra=json_schema_extra, **field_kwargs)


def node_spec(
    *,
    name: str,
    display_name: str,
    description: str,
    category: NodeCategory,
    icon: str,
    llm_hint: str | None = None,
    version: str = "1.0.0",
    examples: list[NodeExample] | tuple[NodeExample, ...] = (),
    graph_constraints: GraphConstraints | None = None,
    property_order: list[str] | tuple[str, ...] = (),
    field_overrides: dict[str, dict[str, Any]] | None = None,
) -> Callable[[type[BaseModel]], type[BaseModel]]:
    metadata = NodeSpecMetadata(
        name=name,
        display_name=display_name,
        description=description,
        category=category,
        icon=icon,
        llm_hint=llm_hint,
        version=version,
        examples=tuple(examples),
        graph_constraints=graph_constraints,
        property_order=tuple(property_order),
        field_overrides=field_overrides or {},
    )

    def decorator(model_cls: type[BaseModel]) -> type[BaseModel]:
        setattr(model_cls, "__node_spec_metadata__", metadata)
        return model_cls

    return decorator


def build_spec(model_cls: type[BaseModel]) -> NodeSpec:
    metadata: NodeSpecMetadata | None = getattr(
        model_cls, "__node_spec_metadata__", None
    )
    if metadata is None:
        raise ValueError(f"{model_cls.__name__} is missing __node_spec_metadata__")

    properties: list[PropertySpec] = []
    for name, field in model_cls.model_fields.items():
        prop = _build_property_spec(model_cls, name, field)
        if prop is not None:
            properties.append(prop)
    properties = _sort_properties(metadata.name, properties, metadata.property_order)

    return NodeSpec(
        name=metadata.name,
        display_name=metadata.display_name,
        description=metadata.description,
        llm_hint=metadata.llm_hint,
        category=metadata.category,
        icon=metadata.icon,
        version=metadata.version,
        properties=properties,
        examples=list(metadata.examples),
        graph_constraints=metadata.graph_constraints,
    )


def _sort_properties(
    spec_name: str,
    properties: list[PropertySpec],
    property_order: tuple[str, ...],
) -> list[PropertySpec]:
    if not property_order:
        return properties

    property_names = {prop.name for prop in properties}
    missing = [name for name in property_order if name not in property_names]
    if missing:
        raise ValueError(
            f"{spec_name}: property_order references unknown properties: {missing}"
        )

    order_map = {name: idx for idx, name in enumerate(property_order)}
    ordered = sorted(
        enumerate(properties),
        key=lambda item: (order_map.get(item[1].name, len(order_map)), item[0]),
    )
    return [prop for _, prop in ordered]


def _build_property_spec(
    owner_cls: type[BaseModel],
    field_name: str,
    field: FieldInfo,
) -> PropertySpec | None:
    meta = _merged_field_meta(owner_cls, field_name, field)
    if meta.get("spec_exclude"):
        return None

    prop_type = _resolve_property_type(field.annotation, meta)
    nested_properties = _resolve_nested_properties(field.annotation, prop_type)
    options = _resolve_options(field.annotation, meta, prop_type)
    min_value, max_value, min_length, max_length, pattern = _resolve_constraints(
        field, meta
    )

    description = meta.get("description") or field.description
    if not description:
        raise ValueError(f"{owner_cls.__name__}.{field_name} is missing a description")

    return PropertySpec(
        name=field_name,
        type=prop_type,
        display_name=meta.get("display_name") or _humanize_identifier(field_name),
        description=description,
        llm_hint=meta.get("llm_hint"),
        default=_resolve_default(field, meta),
        required=_resolve_required(field, meta),
        placeholder=meta.get("placeholder"),
        display_options=meta.get("display_options"),
        options=options,
        properties=nested_properties,
        min_value=min_value,
        max_value=max_value,
        min_length=min_length,
        max_length=max_length,
        pattern=pattern,
        editor=meta.get("editor"),
        extra=meta.get("extra") or {},
    )


def _merged_field_meta(
    owner_cls: type[BaseModel],
    field_name: str,
    field: FieldInfo,
) -> dict[str, Any]:
    field_meta = {}
    if isinstance(field.json_schema_extra, dict):
        field_meta = dict(field.json_schema_extra.get(_SPEC_FIELD_META_KEY, {}) or {})
    metadata: NodeSpecMetadata | None = getattr(
        owner_cls, "__node_spec_metadata__", None
    )
    override = (
        dict(metadata.field_overrides.get(field_name, {}) or {})
        if metadata is not None
        else {}
    )
    merged = dict(field_meta)
    merged.update(override)
    return merged


def _resolve_property_type(annotation: Any, meta: dict[str, Any]) -> PropertyType:
    ui_type = meta.get("ui_type")
    if ui_type:
        return PropertyType(ui_type)

    inner = _strip_optional(annotation)
    origin = get_origin(inner)
    args = get_args(inner)

    if origin is list:
        item_type = _strip_optional(args[0]) if args else Any
        if isinstance(item_type, type) and issubclass(item_type, BaseModel):
            return PropertyType.fixed_collection
        raise ValueError(
            "List-valued fields must declare an explicit ui_type unless they wrap a "
            f"BaseModel row type (field annotation: {annotation!r})."
        )

    if _is_enum(inner) or _is_literal(inner):
        return PropertyType.options

    if inner in (str,):
        return PropertyType.string
    if inner in (int, float):
        return PropertyType.number
    if inner is bool:
        return PropertyType.boolean
    if inner in (dict, Any) or origin is dict:
        return PropertyType.json

    raise ValueError(f"Unable to derive PropertyType for annotation {annotation!r}")


def _resolve_nested_properties(
    annotation: Any,
    prop_type: PropertyType,
) -> list[PropertySpec] | None:
    if prop_type != PropertyType.fixed_collection:
        return None

    inner = _strip_optional(annotation)
    args = get_args(inner)
    if not args:
        raise ValueError(
            f"fixed_collection field annotation is missing row type: {annotation!r}"
        )
    row_type = _strip_optional(args[0])
    if not isinstance(row_type, type) or not issubclass(row_type, BaseModel):
        raise ValueError(
            f"fixed_collection rows must be BaseModel subclasses: {annotation!r}"
        )

    properties: list[PropertySpec] = []
    for field_name, field in row_type.model_fields.items():
        prop = _build_property_spec(row_type, field_name, field)
        if prop is not None:
            properties.append(prop)
    return properties


def _resolve_options(
    annotation: Any,
    meta: dict[str, Any],
    prop_type: PropertyType,
) -> list[PropertyOption] | None:
    if prop_type not in (PropertyType.options, PropertyType.multi_options):
        return meta.get("options")

    if meta.get("options"):
        return meta["options"]

    inner = _strip_optional(annotation)
    if prop_type == PropertyType.multi_options:
        inner = _strip_optional(get_args(inner)[0])

    if _is_enum(inner):
        return [
            PropertyOption(
                value=member.value, label=_humanize_option_label(member.value)
            )
            for member in inner
        ]
    if _is_literal(inner):
        return [
            PropertyOption(value=value, label=_humanize_option_label(value))
            for value in get_args(inner)
            if value is not None
        ]
    return None


def _resolve_constraints(
    field: FieldInfo,
    meta: dict[str, Any],
) -> tuple[float | None, float | None, int | None, int | None, str | None]:
    min_value = meta.get("min_value")
    max_value = meta.get("max_value")
    min_length = meta.get("min_length")
    max_length = meta.get("max_length")
    pattern = meta.get("pattern")

    for item in field.metadata:
        if min_value is None:
            if hasattr(item, "ge") and item.ge is not None:
                min_value = item.ge
            elif hasattr(item, "gt") and item.gt is not None:
                min_value = item.gt
        if max_value is None:
            if hasattr(item, "le") and item.le is not None:
                max_value = item.le
            elif hasattr(item, "lt") and item.lt is not None:
                max_value = item.lt
        if (
            min_length is None
            and hasattr(item, "min_length")
            and item.min_length is not None
        ):
            min_length = item.min_length
        if (
            max_length is None
            and hasattr(item, "max_length")
            and item.max_length is not None
        ):
            max_length = item.max_length
        if pattern is None and hasattr(item, "pattern") and item.pattern is not None:
            pattern = item.pattern

    return min_value, max_value, min_length, max_length, pattern


def _resolve_default(field: FieldInfo, meta: dict[str, Any]) -> Any:
    if "spec_default" in meta:
        return meta["spec_default"]
    if field.default is not PydanticUndefined:
        return field.default
    return None


def _resolve_required(field: FieldInfo, meta: dict[str, Any]) -> bool:
    if meta.get("required") is not None:
        return bool(meta["required"])
    return bool(field.is_required())


def _strip_optional(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is None:
        return annotation

    args = [arg for arg in get_args(annotation) if arg is not NoneType]
    if len(args) == 1 and len(args) != len(get_args(annotation)):
        return args[0]
    return annotation


def _is_enum(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Enum)


def _is_literal(annotation: Any) -> bool:
    return get_origin(annotation) is Literal


def _humanize_identifier(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _humanize_option_label(value: Any) -> str:
    if isinstance(value, str):
        return value.replace("_", " ").replace("-", " ").strip().title()
    return str(value)
