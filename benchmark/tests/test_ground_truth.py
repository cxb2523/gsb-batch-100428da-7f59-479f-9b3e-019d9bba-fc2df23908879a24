import json
import pathlib

import pytest

GROUND_TRUTH_DIR = pathlib.Path(__file__).parent.parent / "ground_truth"

VALID_ENTITY_TYPES = {"PERSON", "ORG", "GPE", "PRODUCT", "LOC", "EVENT", "MONEY"}


def load_json(path: pathlib.Path) -> dict:
    with open(path) as f:
        return json.load(f)


def entity_files() -> list[pathlib.Path]:
    return sorted(GROUND_TRUTH_DIR.glob("*-entities.json"))


def schema_files() -> list[pathlib.Path]:
    return sorted(GROUND_TRUTH_DIR.glob("*-schemas.json"))


class TestEntityAnnotations:
    def test_at_least_10_entity_files_exist(self):
        files = entity_files()
        assert len(files) >= 10, f"Expected >= 10 entity files, found {len(files)}"

    def test_entity_files_have_required_fields(self):
        for path in entity_files():
            data = load_json(path)
            assert "url" in data, f"{path.name} missing 'url'"
            assert "annotator" in data, f"{path.name} missing 'annotator'"
            assert "annotated_at" in data, f"{path.name} missing 'annotated_at'"
            assert "entities" in data, f"{path.name} missing 'entities'"
            assert isinstance(data["entities"], list), f"{path.name} 'entities' is not a list"

    def test_entities_have_text_and_type(self):
        for path in entity_files():
            data = load_json(path)
            for i, entity in enumerate(data["entities"]):
                assert "text" in entity, f"{path.name} entity[{i}] missing 'text'"
                assert "type" in entity, f"{path.name} entity[{i}] missing 'type'"

    def test_entity_types_are_valid(self):
        for path in entity_files():
            data = load_json(path)
            for entity in data["entities"]:
                assert entity["type"] in VALID_ENTITY_TYPES, (
                    f"{path.name}: invalid entity type '{entity['type']}'"
                )

    def test_entity_text_is_nonempty(self):
        for path in entity_files():
            data = load_json(path)
            for entity in data["entities"]:
                assert entity["text"].strip(), f"{path.name}: entity has empty text"


class TestSchemaAnnotations:
    def test_at_least_8_schema_files_exist(self):
        files = schema_files()
        assert len(files) >= 8, f"Expected >= 8 schema files, found {len(files)}"

    def test_schema_files_have_required_fields(self):
        for path in schema_files():
            data = load_json(path)
            assert "url" in data, f"{path.name} missing 'url'"
            assert "schemas" in data, f"{path.name} missing 'schemas'"
            assert isinstance(data["schemas"], list), f"{path.name} 'schemas' is not a list"

    def test_schemas_have_type_and_key_fields(self):
        for path in schema_files():
            data = load_json(path)
            for i, schema in enumerate(data["schemas"]):
                assert "type" in schema, f"{path.name} schema[{i}] missing 'type'"
                assert schema["type"] in ("JSON_LD", "OPENAPI"), (
                    f"{path.name} schema[{i}] invalid type: {schema['type']}"
                )
                assert "key_fields" in schema, f"{path.name} schema[{i}] missing 'key_fields'"
                assert isinstance(schema["key_fields"], list)
                assert "expected_count" in schema, f"{path.name} schema[{i}] missing 'expected_count'"
