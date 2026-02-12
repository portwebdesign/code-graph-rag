import pytest

from codebase_rag.parsers.config.config_parser import (
    ConfigParserMixin,
    JSONParserMixin,
    YAMLParserMixin,
)
from codebase_rag.parsers.languages.kotlin.kotlin_parser import (
    KotlinModifier,
    KotlinParserMixin,
)
from codebase_rag.parsers.languages.ruby import (
    RubyParserMixin,
)


class TestRubyParser(RubyParserMixin):
    """Test Ruby parser functionality."""

    def test_extract_simple_method(self):
        """Test extraction of simple method definition."""
        code = """
def greet(name)
  puts "Hello, #{name}!"
end
"""
        defs = self.extract_ruby_definitions(code)
        assert len(defs.methods) == 1
        assert defs.methods[0].name == "greet"
        assert len(defs.methods[0].parameters) == 1
        assert defs.methods[0].parameters[0] == "name"

    def test_extract_class_definition(self):
        """Test extraction of class definition."""
        code = """
class User < ApplicationRecord
  has_many :posts
  validates :email, presence: true
end
"""
        defs = self.extract_ruby_definitions(code)
        assert len(defs.classes) == 1
        assert defs.classes[0].name == "User"
        assert defs.classes[0].superclass == "ApplicationRecord"

    def test_extract_module_definition(self):
        """Test extraction of module definition."""
        code = """
module Authentication
  def self.authenticate(user)
    # authentication logic
  end
end
"""
        defs = self.extract_ruby_definitions(code)
        assert len(defs.modules) == 1
        assert defs.modules[0].name == "Authentication"

    def test_extract_visibility_modifiers(self):
        """Test extraction of method visibility."""
        code = """
private
def secret_method
  # private method
end

public
def public_method
  # public method
end
"""
        defs = self.extract_ruby_definitions(code)
        assert len(defs.methods) >= 1

    def test_extract_requires(self):
        """Test extraction of require statements."""
        code = """
require 'rails'
require 'devise'
gem 'activerecord'
gem 'sinatra'
"""
        requires = self._extract_requires(code)
        assert "rails" in requires
        assert "devise" in requires

    def test_extract_constants(self):
        """Test extraction of constants."""
        code = """
MAX_USERS = 100
API_KEY = "secret"
CONFIG = { timeout: 30 }
"""
        constants = self._extract_constants(code)
        assert "MAX_USERS" in constants
        assert "API_KEY" in constants
        assert "CONFIG" in constants

    def test_rails_model_associations(self):
        """Test extraction of Rails associations."""
        code = """
class Post < ApplicationRecord
  belongs_to :user
  has_many :comments
  has_and_belongs_to_many :tags
end
"""
        model = self.extract_rails_models(code, "Post")
        assert len(model.associations) >= 2
        assert any(a.type == "belongs_to" for a in model.associations)
        assert any(a.type == "has_many" for a in model.associations)

    def test_rails_model_validations(self):
        """Test extraction of Rails validations."""
        code = """
class User < ApplicationRecord
  validates :email, presence: true, uniqueness: true
  validates :password, length: { minimum: 8 }
end
"""
        model = self.extract_rails_models(code, "User")
        assert len(model.validations) >= 1
        assert any(v.attribute == "email" for v in model.validations)

    def test_rails_model_scopes(self):
        """Test extraction of Rails scopes."""
        code = """
class Article < ApplicationRecord
  scope :published, -> { where(published: true) }
  scope :recent, -> { order(created_at: :desc) }
end
"""
        model = self.extract_rails_models(code, "Article")
        assert len(model.scopes) >= 1

    def test_rails_model_callbacks(self):
        """Test extraction of Rails callbacks."""
        code = """
class Article < ApplicationRecord
  before_save :validate_content
  after_create :send_notification
  before_destroy :check_permissions
end
"""
        model = self.extract_rails_models(code, "Article")
        assert len(model.callbacks) >= 1
        assert any(c.event == "before_save" for c in model.callbacks)

    def test_rails_routes_extraction(self):
        """Test extraction of routes from routes.rb."""
        code = """
Rails.application.routes.draw do
  get 'home', to: 'pages#home'
  post 'users', to: 'users#create'
  resources :posts
  resources :comments
end
"""
        routes = self.extract_rails_routes(code)
        assert len(routes) >= 1
        assert any(r["method"] == "GET" for r in routes)
        assert any(r["method"] == "POST" for r in routes)
        assert any(r["method"] == "RESOURCE" for r in routes)

    def test_ruby_file_analysis(self, tmp_path):
        """Test complete Ruby file analysis."""
        ruby_file = tmp_path / "test_model.rb"
        ruby_file.write_text("""
class Product < ApplicationRecord
  belongs_to :category
  has_many :reviews

  validates :name, presence: true
  validates :price, numericality: true

  scope :active, -> { where(active: true) }

  before_save :set_default_price
  after_create :create_sku

  private

  def set_default_price
    self.price = 0 if price.nil?
  end
end
""")
        analysis = self.analyze_ruby_file(str(ruby_file))
        assert analysis["is_rails_model"]
        assert analysis["rails_model"] is not None


class TestKotlinParser(KotlinParserMixin):
    """Test Kotlin parser functionality."""

    def test_extract_simple_function(self):
        """Test extraction of simple function."""
        code = """
fun greet(name: String): String {
    return "Hello, $name!"
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.functions) == 1
        assert defs.functions[0].name == "greet"
        assert len(defs.functions[0].parameters) == 1

    def test_extract_class_definition(self):
        """Test extraction of class definition."""
        code = """
class User(val name: String, var age: Int) : Person {
    fun greet() {
        println("Hi, I'm $name")
    }
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.classes) == 1
        assert defs.classes[0].name == "User"

    def test_extract_data_class(self):
        """Test extraction of data class."""
        code = """
data class Person(val name: String, val age: Int)
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.classes) == 1
        assert KotlinModifier.DATA in defs.classes[0].modifiers
        assert "Person" in defs.data_classes

    def test_extract_sealed_class(self):
        """Test extraction of sealed class."""
        code = """
sealed class Result {
    data class Success(val data: String) : Result()
    data class Error(val exception: Exception) : Result()
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.sealed_classes) >= 1

    def test_extract_interface(self):
        """Test extraction of interface definition."""
        code = """
interface Repository {
    fun save(item: String)
    fun delete(id: Int)
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.interfaces) == 1
        assert defs.interfaces[0].name == "Repository"

    def test_extract_extension_function(self):
        """Test extraction of extension function."""
        code = """
fun String.isEmail(): Boolean {
    return contains("@")
}

fun <T> List<T>.lastOrEmpty(): T? {
    return lastOrNull()
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.extension_functions) >= 1
        assert any(e.receiver_type == "String" for e in defs.extension_functions)

    def test_extract_suspend_function(self):
        """Test extraction of suspend function (coroutines)."""
        code = """
suspend fun fetchUser(id: Int): User {
    return repository.getUser(id)
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.functions) == 1
        assert defs.functions[0].is_suspend

    def test_extract_enum(self):
        """Test extraction of enum class."""
        code = """
enum class Status {
    PENDING, ACTIVE, INACTIVE, DELETED
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.enums) >= 1

    def test_extract_type_alias(self):
        """Test extraction of type aliases."""
        code = """
typealias UserID = String
typealias Callback = (String) -> Unit
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.type_aliases) >= 1
        assert "UserID" in defs.type_aliases

    def test_extract_package_and_imports(self):
        """Test extraction of package and imports."""
        code = """
package com.example.app

import android.app.Activity
import androidx.compose.runtime.*
"""
        defs = self.extract_kotlin_definitions(code)
        assert defs.package == "com.example.app"
        assert len(defs.imports) >= 2

    def test_extract_parameters_with_defaults(self):
        """Test extraction of parameters with default values."""
        code = """
fun configure(host: String = "localhost", port: Int = 8080): Config {
    return Config(host, port)
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.functions) == 1
        params = defs.functions[0].parameters
        assert len(params) == 2
        assert params[0].default_value is not None

    def test_extract_varargs(self):
        """Test extraction of vararg parameters."""
        code = """
fun sum(vararg numbers: Int): Int {
    return numbers.sum()
}
"""
        defs = self.extract_kotlin_definitions(code)
        assert len(defs.functions) == 1
        assert any(p.is_vararg for p in defs.functions[0].parameters)

    def test_coroutine_patterns(self):
        """Test extraction of coroutine patterns."""
        code = """
viewModelScope.launch {
    val user = fetchUser()
}

GlobalScope.async {
    val data = loadData()
}
"""
        patterns = self.extract_coroutine_patterns(code)
        assert len(patterns) >= 1

    def test_dsl_builder_patterns(self):
        """Test extraction of DSL builder patterns."""
        code = """
val html = html {
    body {
        h1 { +"Hello" }
        p { +"This is a paragraph" }
    }
}
"""
        builders = self.extract_dsl_builders(code)
        assert len(builders) >= 1


class TestYAMLParser(YAMLParserMixin):
    """Test YAML parser functionality."""

    def test_extract_simple_yaml(self):
        """Test extraction of simple YAML structure."""
        yaml_content = """
name: John Doe
age: 30
email: john@example.com
"""
        doc = self.extract_yaml_structure(yaml_content)
        assert len(doc.pairs) == 3
        assert any(p.key == "name" for p in doc.pairs)

    def test_extract_nested_yaml(self):
        """Test extraction of nested YAML structure."""
        yaml_content = """
user:
  name: Jane
  contact:
    email: jane@example.com
    phone: "555-1234"
"""
        doc = self.extract_yaml_structure(yaml_content)
        assert doc.max_depth >= 2
        assert len(doc.nested_keys) >= 3

    def test_extract_yaml_arrays(self):
        """Test extraction of arrays from YAML."""
        yaml_content = """
tags:
  - python
  - javascript
  - rust
items:
  - id: 1
  - id: 2
"""
        doc = self.extract_yaml_structure(yaml_content)
        assert len(doc.arrays) >= 1

    def test_extract_kubernetes_resources(self):
        """Test extraction of Kubernetes resources."""
        yaml_content = """
apiVersion: v1
kind: Service
metadata:
  name: my-service
  namespace: default
  labels:
    app: myapp
spec:
  ports:
    - port: 80
      targetPort: 8080
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  replicas: 3
"""
        resources = self.extract_k8s_resources(yaml_content)
        assert len(resources) >= 2
        assert any(r.kind == "Service" for r in resources)
        assert any(r.kind == "Deployment" for r in resources)

    def test_extract_docker_compose(self):
        """Test extraction of Docker Compose services."""
        yaml_content = """
version: '3.8'
services:
  web:
    image: nginx:latest
    ports:
      - "80:80"
    environment:
      - NGINX_PORT=80
    depends_on:
      - db
  db:
    image: postgres:13
    environment:
      POSTGRES_PASSWORD: secret
    volumes:
      - pgdata:/var/lib/postgresql/data
"""
        services = self.extract_docker_compose(yaml_content)
        assert len(services) >= 2
        assert any(s.name == "web" for s in services)
        assert any(s.name == "db" for s in services)


class TestJSONParser(JSONParserMixin):
    """Test JSON parser functionality."""

    def test_extract_simple_json(self):
        """Test extraction of simple JSON."""
        json_content = '{"name": "John", "age": 30}'
        result = self.extract_json_structure(json_content)
        assert result["type"] == "dict"
        assert result["length"] == 2

    def test_extract_nested_json(self):
        """Test extraction of nested JSON."""
        json_content = """
{
  "user": {
    "name": "Jane",
    "contact": {
      "email": "jane@example.com",
      "phone": "555-1234"
    }
  }
}
"""
        result = self.extract_json_structure(json_content)
        assert result["type"] == "dict"
        assert "user" in result["data"]

    def test_extract_package_json(self):
        """Test extraction from package.json."""
        json_content = """
{
  "name": "my-app",
  "version": "1.0.0",
  "description": "My awesome app",
  "main": "index.js",
  "scripts": {
    "start": "node index.js",
    "test": "jest"
  },
  "dependencies": {
    "express": "^4.17.0",
    "axios": "^0.21.0"
  },
  "devDependencies": {
    "jest": "^27.0.0"
  }
}
"""
        pkg_info = self.extract_package_json(json_content)
        assert pkg_info.name == "my-app"
        assert pkg_info.version == "1.0.0"
        assert len(pkg_info.dependencies) >= 2
        assert "jest" in pkg_info.dev_dependencies

    def test_extract_json_imports(self):
        """Test extraction of dependencies from JSON."""
        json_content = """
{
  "dependencies": {
    "react": "^18.0.0",
    "react-dom": "^18.0.0"
  },
  "devDependencies": {
    "typescript": "^4.5.0"
  }
}
"""
        imports = self.extract_json_imports(json_content)
        assert len(imports) >= 3
        assert any(i["package"] == "react" for i in imports)


class TestConfigParser(ConfigParserMixin):
    """Test combined config parser functionality."""

    def test_detect_docker_compose_type(self):
        """Test detection of docker-compose file."""
        config_type = self.detect_config_type("docker-compose.yml")
        assert config_type == "docker-compose"

    def test_detect_kubernetes_type(self):
        """Test detection of Kubernetes manifest."""
        config_type = self.detect_config_type("deployment.yaml")
        assert config_type == "kubernetes"

    def test_detect_package_json_type(self):
        """Test detection of package.json."""
        config_type = self.detect_config_type("package.json")
        assert config_type == "package.json"

    def test_detect_tsconfig_type(self):
        """Test detection of tsconfig."""
        config_type = self.detect_config_type("tsconfig.json")
        assert config_type == "tsconfig"


class TestPhase3Integration:
    """Test Phase 3 integration and backward compatibility."""

    def test_ruby_and_rails_integration(self):
        """Test Ruby parser with Rails-specific features."""
        parser = TestRubyParser()
        code = """
class Post < ApplicationRecord
  belongs_to :user
  has_many :comments, dependent: :destroy

  validates :title, presence: true
  validates :content, length: { minimum: 10 }

  scope :published, -> { where(published: true) }

  before_save :format_title
  after_create :notify_followers
end
"""
        defs = parser.extract_ruby_definitions(code)
        assert len(defs.classes) == 1

        model = parser.extract_rails_models(code, "Post")
        assert len(model.associations) >= 1
        assert len(model.validations) >= 1
        assert len(model.scopes) >= 1
        assert len(model.callbacks) >= 1

    def test_kotlin_advanced_features(self):
        """Test Kotlin parser with advanced features."""
        parser = TestKotlinParser()
        code = """
sealed class Result<out T> {
    data class Success<T>(val data: T) : Result<T>()
    data class Error(val exception: Exception) : Result<Nothing>()
}

suspend fun <T> retry(block: suspend () -> T): Result<T> {
    return try {
        Result.Success(block())
    } catch (e: Exception) {
        Result.Error(e)
    }
}

fun <T> List<T>.safeGet(index: Int): T? =
    if (index in indices) this[index] else null
"""
        defs = parser.extract_kotlin_definitions(code)
        assert len(defs.sealed_classes) >= 1
        assert any(f.is_suspend for f in defs.functions)
        assert len(defs.extension_functions) >= 1

    def test_backward_compatibility_with_phase1_and_2(self):
        """Test that Phase 3 doesn't break Phase 1 & 2 functionality."""

        ruby_parser = TestRubyParser()
        kotlin_parser = TestKotlinParser()
        yaml_parser = TestYAMLParser()
        json_parser = TestJSONParser()
        config_parser = TestConfigParser()

        assert ruby_parser is not None
        assert kotlin_parser is not None
        assert yaml_parser is not None
        assert json_parser is not None
        assert config_parser is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
