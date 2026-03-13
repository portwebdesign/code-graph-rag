from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GraphCodeIndex(_message.Message):
    __slots__ = ("nodes", "relationships")
    NODES_FIELD_NUMBER: _ClassVar[int]
    RELATIONSHIPS_FIELD_NUMBER: _ClassVar[int]
    nodes: _containers.RepeatedCompositeFieldContainer[Node]
    relationships: _containers.RepeatedCompositeFieldContainer[Relationship]
    def __init__(self, nodes: _Optional[_Iterable[_Union[Node, _Mapping]]] = ..., relationships: _Optional[_Iterable[_Union[Relationship, _Mapping]]] = ...) -> None: ...

class Node(_message.Message):
    __slots__ = ("project", "package", "folder", "module", "class_node", "function", "method", "file", "external_package", "module_implementation", "module_interface")
    PROJECT_FIELD_NUMBER: _ClassVar[int]
    PACKAGE_FIELD_NUMBER: _ClassVar[int]
    FOLDER_FIELD_NUMBER: _ClassVar[int]
    MODULE_FIELD_NUMBER: _ClassVar[int]
    CLASS_NODE_FIELD_NUMBER: _ClassVar[int]
    FUNCTION_FIELD_NUMBER: _ClassVar[int]
    METHOD_FIELD_NUMBER: _ClassVar[int]
    FILE_FIELD_NUMBER: _ClassVar[int]
    EXTERNAL_PACKAGE_FIELD_NUMBER: _ClassVar[int]
    MODULE_IMPLEMENTATION_FIELD_NUMBER: _ClassVar[int]
    MODULE_INTERFACE_FIELD_NUMBER: _ClassVar[int]
    project: Project
    package: Package
    folder: Folder
    module: Module
    class_node: Class
    function: Function
    method: Method
    file: File
    external_package: ExternalPackage
    module_implementation: ModuleImplementation
    module_interface: ModuleInterface
    def __init__(self, project: _Optional[_Union[Project, _Mapping]] = ..., package: _Optional[_Union[Package, _Mapping]] = ..., folder: _Optional[_Union[Folder, _Mapping]] = ..., module: _Optional[_Union[Module, _Mapping]] = ..., class_node: _Optional[_Union[Class, _Mapping]] = ..., function: _Optional[_Union[Function, _Mapping]] = ..., method: _Optional[_Union[Method, _Mapping]] = ..., file: _Optional[_Union[File, _Mapping]] = ..., external_package: _Optional[_Union[ExternalPackage, _Mapping]] = ..., module_implementation: _Optional[_Union[ModuleImplementation, _Mapping]] = ..., module_interface: _Optional[_Union[ModuleInterface, _Mapping]] = ...) -> None: ...

class Relationship(_message.Message):
    __slots__ = ("type", "source_id", "target_id", "properties", "source_label", "target_label")
    class RelationshipType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        RELATIONSHIP_TYPE_UNSPECIFIED: _ClassVar[Relationship.RelationshipType]
        CONTAINS_PACKAGE: _ClassVar[Relationship.RelationshipType]
        CONTAINS_FOLDER: _ClassVar[Relationship.RelationshipType]
        CONTAINS_FILE: _ClassVar[Relationship.RelationshipType]
        CONTAINS_MODULE: _ClassVar[Relationship.RelationshipType]
        DEFINES: _ClassVar[Relationship.RelationshipType]
        DEFINES_METHOD: _ClassVar[Relationship.RelationshipType]
        IMPORTS: _ClassVar[Relationship.RelationshipType]
        INHERITS: _ClassVar[Relationship.RelationshipType]
        OVERRIDES: _ClassVar[Relationship.RelationshipType]
        CALLS: _ClassVar[Relationship.RelationshipType]
        DEPENDS_ON_EXTERNAL: _ClassVar[Relationship.RelationshipType]
        IMPLEMENTS_MODULE: _ClassVar[Relationship.RelationshipType]
        IMPLEMENTS: _ClassVar[Relationship.RelationshipType]
        HAS_ENDPOINT: _ClassVar[Relationship.RelationshipType]
        ROUTES_TO_CONTROLLER: _ClassVar[Relationship.RelationshipType]
        ROUTES_TO_ACTION: _ClassVar[Relationship.RelationshipType]
        REQUESTS_ENDPOINT: _ClassVar[Relationship.RelationshipType]
        INCLUDES_ROUTER: _ClassVar[Relationship.RelationshipType]
        MOUNTS_ROUTER: _ClassVar[Relationship.RelationshipType]
        EXPOSES_ENDPOINT: _ClassVar[Relationship.RelationshipType]
        PREFIXES_ENDPOINT: _ClassVar[Relationship.RelationshipType]
    RELATIONSHIP_TYPE_UNSPECIFIED: Relationship.RelationshipType
    CONTAINS_PACKAGE: Relationship.RelationshipType
    CONTAINS_FOLDER: Relationship.RelationshipType
    CONTAINS_FILE: Relationship.RelationshipType
    CONTAINS_MODULE: Relationship.RelationshipType
    DEFINES: Relationship.RelationshipType
    DEFINES_METHOD: Relationship.RelationshipType
    IMPORTS: Relationship.RelationshipType
    INHERITS: Relationship.RelationshipType
    OVERRIDES: Relationship.RelationshipType
    CALLS: Relationship.RelationshipType
    DEPENDS_ON_EXTERNAL: Relationship.RelationshipType
    IMPLEMENTS_MODULE: Relationship.RelationshipType
    IMPLEMENTS: Relationship.RelationshipType
    HAS_ENDPOINT: Relationship.RelationshipType
    ROUTES_TO_CONTROLLER: Relationship.RelationshipType
    ROUTES_TO_ACTION: Relationship.RelationshipType
    REQUESTS_ENDPOINT: Relationship.RelationshipType
    INCLUDES_ROUTER: Relationship.RelationshipType
    MOUNTS_ROUTER: Relationship.RelationshipType
    EXPOSES_ENDPOINT: Relationship.RelationshipType
    PREFIXES_ENDPOINT: Relationship.RelationshipType
    TYPE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_ID_FIELD_NUMBER: _ClassVar[int]
    PROPERTIES_FIELD_NUMBER: _ClassVar[int]
    SOURCE_LABEL_FIELD_NUMBER: _ClassVar[int]
    TARGET_LABEL_FIELD_NUMBER: _ClassVar[int]
    type: Relationship.RelationshipType
    source_id: str
    target_id: str
    properties: _struct_pb2.Struct
    source_label: str
    target_label: str
    def __init__(self, type: _Optional[_Union[Relationship.RelationshipType, str]] = ..., source_id: _Optional[str] = ..., target_id: _Optional[str] = ..., properties: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., source_label: _Optional[str] = ..., target_label: _Optional[str] = ...) -> None: ...

class Project(_message.Message):
    __slots__ = ("name",)
    NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    def __init__(self, name: _Optional[str] = ...) -> None: ...

class Package(_message.Message):
    __slots__ = ("qualified_name", "name", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class Folder(_message.Message):
    __slots__ = ("path", "name", "project_name", "folder_path", "folder_name")
    PATH_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    path: str
    name: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, path: _Optional[str] = ..., name: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class File(_message.Message):
    __slots__ = ("path", "name", "extension", "project_name", "folder_path", "folder_name")
    PATH_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    EXTENSION_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    path: str
    name: str
    extension: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, path: _Optional[str] = ..., name: _Optional[str] = ..., extension: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class Module(_message.Message):
    __slots__ = ("qualified_name", "name", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class ModuleImplementation(_message.Message):
    __slots__ = ("qualified_name", "name", "path", "implements_module", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    IMPLEMENTS_MODULE_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    path: str
    implements_module: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., path: _Optional[str] = ..., implements_module: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class ModuleInterface(_message.Message):
    __slots__ = ("qualified_name", "name", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class ExternalPackage(_message.Message):
    __slots__ = ("name", "project_name")
    NAME_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    name: str
    project_name: str
    def __init__(self, name: _Optional[str] = ..., project_name: _Optional[str] = ...) -> None: ...

class Function(_message.Message):
    __slots__ = ("qualified_name", "name", "docstring", "start_line", "end_line", "decorators", "is_exported", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DOCSTRING_FIELD_NUMBER: _ClassVar[int]
    START_LINE_FIELD_NUMBER: _ClassVar[int]
    END_LINE_FIELD_NUMBER: _ClassVar[int]
    DECORATORS_FIELD_NUMBER: _ClassVar[int]
    IS_EXPORTED_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    docstring: str
    start_line: int
    end_line: int
    decorators: _containers.RepeatedScalarFieldContainer[str]
    is_exported: bool
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., docstring: _Optional[str] = ..., start_line: _Optional[int] = ..., end_line: _Optional[int] = ..., decorators: _Optional[_Iterable[str]] = ..., is_exported: bool = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class Method(_message.Message):
    __slots__ = ("qualified_name", "name", "docstring", "start_line", "end_line", "decorators", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DOCSTRING_FIELD_NUMBER: _ClassVar[int]
    START_LINE_FIELD_NUMBER: _ClassVar[int]
    END_LINE_FIELD_NUMBER: _ClassVar[int]
    DECORATORS_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    docstring: str
    start_line: int
    end_line: int
    decorators: _containers.RepeatedScalarFieldContainer[str]
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., docstring: _Optional[str] = ..., start_line: _Optional[int] = ..., end_line: _Optional[int] = ..., decorators: _Optional[_Iterable[str]] = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...

class Class(_message.Message):
    __slots__ = ("qualified_name", "name", "docstring", "start_line", "end_line", "decorators", "is_exported", "path", "project_name", "folder_path", "folder_name")
    QUALIFIED_NAME_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    DOCSTRING_FIELD_NUMBER: _ClassVar[int]
    START_LINE_FIELD_NUMBER: _ClassVar[int]
    END_LINE_FIELD_NUMBER: _ClassVar[int]
    DECORATORS_FIELD_NUMBER: _ClassVar[int]
    IS_EXPORTED_FIELD_NUMBER: _ClassVar[int]
    PATH_FIELD_NUMBER: _ClassVar[int]
    PROJECT_NAME_FIELD_NUMBER: _ClassVar[int]
    FOLDER_PATH_FIELD_NUMBER: _ClassVar[int]
    FOLDER_NAME_FIELD_NUMBER: _ClassVar[int]
    qualified_name: str
    name: str
    docstring: str
    start_line: int
    end_line: int
    decorators: _containers.RepeatedScalarFieldContainer[str]
    is_exported: bool
    path: str
    project_name: str
    folder_path: str
    folder_name: str
    def __init__(self, qualified_name: _Optional[str] = ..., name: _Optional[str] = ..., docstring: _Optional[str] = ..., start_line: _Optional[int] = ..., end_line: _Optional[int] = ..., decorators: _Optional[_Iterable[str]] = ..., is_exported: bool = ..., path: _Optional[str] = ..., project_name: _Optional[str] = ..., folder_path: _Optional[str] = ..., folder_name: _Optional[str] = ...) -> None: ...
