from pathlib import Path

import pytest

from codebase_rag.parsers.embedding_strategies import (
    EmbeddingPayload,
    EmbeddingStrategy,
    EmbeddingTextExtractor,
    NodeInfo,
)
from codebase_rag.parsers.framework_detectors import (
    JavaFrameworkDetector,
    JavaFrameworkType,
    JsFrameworkDetector,
    JsFrameworkType,
    PythonFrameworkDetector,
    PythonFrameworkType,
    RubyFrameworkDetector,
    RubyFrameworkType,
)


class TestPythonFrameworkDetector:
    """Tests for Python framework detection."""

    @pytest.fixture
    def detector(self):
        """Create detector instance."""
        return PythonFrameworkDetector()

    def test_django_detection(self, detector):
        """Test Django framework detection."""
        code = """
import django
from django.db import models
from django.views import View

class UserModel(models.Model):
    name = models.CharField(max_length=100)
"""
        framework = detector.detect_framework(None, code)
        assert framework == PythonFrameworkType.DJANGO

    def test_flask_detection(self, detector):
        """Test Flask framework detection."""
        code = """
from flask import Flask, request

app = Flask(__name__)

@app.route('/users', methods=['GET', 'POST'])
def get_users():
    return {'users': []}
"""
        framework = detector.detect_framework(None, code)
        assert framework == PythonFrameworkType.FLASK

    def test_fastapi_detection(self, detector):
        """Test FastAPI framework detection."""
        code = """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

@app.get("/items/{item_id}")
async def read_item(item_id: int):
    return {"item_id": item_id}
"""
        framework = detector.detect_framework(None, code)
        assert framework == PythonFrameworkType.FASTAPI

    def test_extract_django_endpoints(self, detector):
        """Test Django endpoint extraction."""
        code = """
from django.urls import path
from . import views

urlpatterns = [
    path('users/', views.UserList.as_view(), name='user-list'),
    path('users/<int:pk>/', views.UserDetail.as_view(), name='user-detail'),
]
"""
        endpoints = detector.extract_django_endpoints(None, code)
        assert len(endpoints) >= 2
        assert any("users" in ep.path for ep in endpoints)

    def test_extract_flask_routes(self, detector):
        """Test Flask route extraction."""
        code = """
@app.route('/api/users', methods=['GET', 'POST'])
def list_users():
    return {'users': []}

@app.route('/api/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
    return {'user_id': user_id}
"""
        routes = detector.extract_flask_routes(None, code)
        assert len(routes) == 2
        assert any(route.path == "/api/users" for route in routes)

    def test_extract_django_models(self, detector):
        """Test Django model extraction."""
        code = """
from django.db import models

class User(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'users'
"""
        models = detector.extract_django_models(None, code)
        assert len(models) == 1
        assert models[0].name == "User"
        assert "name" in models[0].fields
        assert "email" in models[0].fields

    def test_framework_metadata(self, detector):
        """Test framework metadata extraction."""
        code = """
from django.shortcuts import render
from django.views import View
from django.http import JsonResponse

class UserView(View):
    @require_http_methods(["GET"])
    def get(self, request):
        return JsonResponse({'users': []})
"""
        framework = detector.detect_framework(None, code)
        metadata = detector.get_framework_metadata(framework, None, code)

        assert metadata["framework_type"] == "django"
        assert metadata["detected"] is True


class TestJavaFrameworkDetector:
    """Tests for Java framework detection."""

    @pytest.fixture
    def detector(self):
        """Create detector instance."""
        return JavaFrameworkDetector()

    def test_spring_boot_detection(self, detector):
        """Test Spring Boot framework detection."""
        code = """
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
"""
        framework = detector.detect_framework(code)
        assert framework == JavaFrameworkType.SPRING_BOOT

    def test_spring_mvc_detection(self, detector):
        """Test Spring MVC detection."""
        code = """
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.*;

@Controller
@RequestMapping("/api/users")
public class UserController {
    @GetMapping
    public List<User> getUsers() {
        return userService.findAll();
    }
}
"""
        framework = detector.detect_framework(code)
        assert framework == JavaFrameworkType.SPRING_MVC

    def test_extract_endpoints(self, detector):
        """Test endpoint extraction."""
        code = """
@RestController
@RequestMapping("/api/users")
public class UserController {
    @GetMapping
    public List<User> listUsers() {
        return userService.findAll();
    }

    @GetMapping("/{id}")
    public User getUser(@PathVariable Long id) {
        return userService.findById(id);
    }

    @PostMapping
    public User createUser(@RequestBody User user) {
        return userService.save(user);
    }
}
"""
        endpoints = detector.extract_endpoints(code)
        assert len(endpoints) >= 2
        assert any(ep.method == "GET" for ep in endpoints)
        assert any(ep.method == "POST" for ep in endpoints)

    def test_extract_entities(self, detector):
        """Test JPA entity extraction."""
        code = """
@Entity
@Table(name = "users")
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private String name;

    private String email;
}
"""
        entities = detector.extract_entities(code)
        assert len(entities) == 1
        assert entities[0].class_name == "User"
        assert entities[0].table_name == "users"

    def test_extract_repositories(self, detector):
        """Test repository extraction."""
        code = """
@Repository
public interface UserRepository extends JpaRepository<User, Long> {
    List<User> findByName(String name);
    Optional<User> findByEmail(String email);
}
"""
        repos = detector.extract_repositories(code)
        assert len(repos) == 1
        assert repos[0].class_name == "UserRepository"
        assert repos[0].entity_type == "User"


class TestRubyFrameworkDetector:
    """Tests for Ruby framework detection."""

    @pytest.fixture
    def detector(self):
        """Create detector instance."""
        return RubyFrameworkDetector()

    def test_rails_detection_from_source(self, detector):
        """Test Rails detection from source code."""
        code = """
class User < ApplicationRecord
  has_many :posts
  validates :email, presence: true, uniqueness: true
end
"""
        framework = detector.detect_from_source(code)
        assert framework == RubyFrameworkType.RAILS

    def test_sinatra_detection(self, detector):
        """Test Sinatra detection."""
        code = """
require 'sinatra'

get '/users' do
  @users = User.all
  erb :users
end

post '/users' do
  User.create(params[:user])
end
"""
        framework = detector.detect_from_source(code)
        assert framework == RubyFrameworkType.SINATRA

    def test_extract_rails_routes(self, detector):
        """Test Rails route extraction."""
        routes_content = """
Rails.application.routes.draw do
  resources :users
  resources :posts do
    resources :comments
  end

  get 'dashboard', to: 'dashboard#index'
  post 'login', to: 'sessions#create'
end
"""
        routes_file = Path("temp_routes.rb")
        routes_file.write_text(routes_content)

        try:
            routes = detector.extract_rails_routes(routes_file)
            assert len(routes) > 0
        finally:
            routes_file.unlink()

    def test_extract_rails_models(self, detector):
        """Test Rails model extraction."""
        code = """
class Post < ApplicationRecord
  belongs_to :user
  has_many :comments, dependent: :destroy

  validates :title, presence: true
  validates :body, length: { minimum: 10 }

  before_save :set_slug
  after_create :notify_admin

  scope :published, -> { where(published: true) }
  scope :recent, -> { order(created_at: :desc) }

  def set_slug
    self.slug = title.downcase.gsub(' ', '-')
  end
end
"""
        models = detector.extract_rails_models(code)
        assert len(models) == 1
        assert models[0].class_name == "Post"
        assert len(models[0].associations) > 0
        assert len(models[0].validations) > 0


class TestJsFrameworkDetector:
    """Tests for JavaScript/TypeScript framework detection."""

    @pytest.fixture
    def detector(self):
        """Create detector instance."""
        return JsFrameworkDetector()

    def test_react_detection(self, detector):
        """Test React detection."""
        code = """
import React, { useState } from 'react';

function UserList({ users }) {
  const [filter, setFilter] = useState('');

  return (
    <div>
      <h1>Users</h1>
      <ul>
        {users.map(user => <li key={user.id}>{user.name}</li>)}
      </ul>
    </div>
  );
}

export default UserList;
"""
        framework = detector.detect_from_source(code)
        assert framework == JsFrameworkType.REACT

    def test_express_detection(self, detector):
        """Test Express detection."""
        code = """
const express = require('express');
const app = express();

app.get('/api/users', (req, res) => {
  res.json({ users: [] });
});

app.post('/api/users', (req, res) => {
  // Create user
});

app.listen(3000);
"""
        framework = detector.detect_from_source(code)
        assert framework == JsFrameworkType.EXPRESS

    def test_nestjs_detection(self, detector):
        """Test NestJS detection."""
        code = """
import { Controller, Get, Post, Body } from '@nestjs/common';
import { UsersService } from './users.service';

@Controller('users')
export class UsersController {
  constructor(private usersService: UsersService) {}

  @Get()
  findAll() {
    return this.usersService.findAll();
  }

  @Post()
  create(@Body() createUserDto) {
    return this.usersService.create(createUserDto);
  }
}
"""
        framework = detector.detect_from_source(code)
        assert framework == JsFrameworkType.NESTJS

    def test_extract_react_components(self, detector):
        """Test React component extraction."""
        code = """
function UserProfile({ userId, userName }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    loadData();
  }, [userId]);

  return <div>{data}</div>;
}
"""
        components = detector.extract_react_components(code)
        assert len(components) >= 1

    def test_extract_express_routes(self, detector):
        """Test Express route extraction."""
        code = """
app.get('/api/users', handler);
app.post('/api/users', createHandler);
app.put('/api/users/:id', updateHandler);
app.delete('/api/users/:id', deleteHandler);

router.get('/posts', listPosts);
router.post('/posts', createPost);
"""
        routes = detector.extract_express_routes(code)
        assert len(routes) >= 4
        assert any(r.method == "GET" for r in routes)
        assert any(r.method == "POST" for r in routes)


class TestEmbeddingTextExtractor:
    """Tests for embedding text extraction strategies."""

    @pytest.fixture
    def extractor(self):
        """Create extractor instance."""
        return EmbeddingTextExtractor()

    @pytest.fixture
    def sample_node(self):
        """Create sample node information."""
        return NodeInfo(
            node_id="func_123",
            kind="function",
            name="calculate_total",
            signature="def calculate_total(items: List[Item], tax: float = 0.1) -> float:",
            docstring="Calculate total price of items including tax.\n\nArgs:\n    items: List of items\n    tax: Tax rate (default 0.1)\n\nReturns:\n    Total price including tax",
            body_text="subtotal = sum(item.price for item in items)\nreturn subtotal * (1 + tax)",
            parameters=["items", "tax"],
            return_type="float",
            decorators=["@property", "@cache"],
            parent_class="PriceCalculator",
            start_line=10,
            end_line=15,
        )

    def test_raw_extraction(self, extractor, sample_node):
        """Test RAW extraction strategy."""
        payload = extractor.extract(sample_node, EmbeddingStrategy.RAW)

        assert isinstance(payload, EmbeddingPayload)
        assert "subtotal = sum" in payload.text
        assert "def calculate_total" not in payload.text
        assert len(payload.text) < 200
        assert payload.metadata["strategy"] == "raw"

    def test_semantic_extraction(self, extractor, sample_node):
        """Test SEMANTIC extraction strategy."""
        payload = extractor.extract(sample_node, EmbeddingStrategy.SEMANTIC)

        assert "Signature:" in payload.text
        assert "Documentation:" in payload.text
        assert "Implementation:" in payload.text
        assert "def calculate_total" in payload.text
        assert "Calculate total price" in payload.text
        assert payload.metadata["strategy"] == "semantic"
        assert payload.metadata["has_docstring"] is True
        assert payload.metadata["has_signature"] is True

    def test_rich_extraction(self, extractor, sample_node):
        """Test RICH extraction strategy."""
        payload = extractor.extract(
            sample_node, EmbeddingStrategy.RICH, framework="django"
        )

        assert "Type Information:" in payload.text
        assert "Decorators:" in payload.text
        assert payload.metadata["strategy"] == "rich"
        assert payload.metadata["decorators_count"] == 2
        assert payload.metadata["has_parent_class"] is True
        assert payload.framework == "django"

    def test_payload_summary(self, extractor, sample_node):
        """Test payload summary generation."""
        payload = extractor.extract(sample_node, EmbeddingStrategy.SEMANTIC)
        summary = payload.get_summary()

        assert "function" in summary.lower()
        assert "calculate_total" in summary or "chars" in summary

    def test_extract_from_dict(self, extractor):
        """Test extraction from dictionary."""
        node_dict = {
            "node_id": "method_456",
            "kind": "method",
            "name": "process_payment",
            "signature": "def process_payment(amount: Decimal, card: str) -> str:",
            "docstring": "Process payment transaction.",
            "body_text": "return gateway.charge(amount, card)",
            "parameters": ["amount", "card"],
            "return_type": "str",
        }

        payload = extractor.extract_from_dict(
            node_dict, EmbeddingStrategy.SEMANTIC, framework="flask"
        )

        assert payload.entity_type == "method"
        assert payload.framework == "flask"
        assert "process_payment" in payload.text

    def test_code_metrics_computation(self, extractor, sample_node):
        """Test code metrics computation."""
        payload = extractor.extract(sample_node, EmbeddingStrategy.RICH)

        metrics = payload.metadata.get("metrics", {})
        assert "lines_of_code" in metrics
        assert metrics["lines_of_code"] == 6


class TestPhase2Integration:
    """Integration tests for Phase 2 components."""

    def test_python_framework_and_embedding(self):
        """Test Python framework detection with embedding extraction."""
        py_detector = PythonFrameworkDetector()
        extractor = EmbeddingTextExtractor()

        code = """
class UserView(View):
    def get(self, request):
        '''Get user list from database.'''
        return JsonResponse(User.objects.all())
"""

        framework = py_detector.detect_framework(None, code)
        assert framework != PythonFrameworkType.NONE

        node_info = NodeInfo(
            node_id="view_1",
            kind="view",
            name="UserView.get",
            docstring="Get user list from database.",
            body_text="return JsonResponse(User.objects.all())",
        )

        payload = extractor.extract(
            node_info, EmbeddingStrategy.RICH, framework=framework.value
        )

        assert payload.framework == "django"
        assert "Framework Context" in payload.text

    def test_java_framework_and_embedding(self):
        """Test Java framework detection with embedding extraction."""
        java_detector = JavaFrameworkDetector()
        extractor = EmbeddingTextExtractor()

        code = """
@RestController
@RequestMapping("/api/users")
public class UserController {
    @GetMapping
    public List<User> getAllUsers() {
        return userService.findAll();
    }
}
"""

        framework = java_detector.detect_framework(code)
        assert framework in [
            JavaFrameworkType.SPRING_BOOT,
            JavaFrameworkType.SPRING_MVC,
        ]

        node_info = NodeInfo(
            node_id="method_1",
            kind="method",
            name="getAllUsers",
            parent_class="UserController",
        )

        payload = extractor.extract(
            node_info,
            EmbeddingStrategy.RICH,
            framework=framework.value,
            language="java",
        )

        assert "Spring" in payload.text or payload.language == "java"

    def test_backward_compatibility(self):
        """Test that Phase 2 doesn't break existing code."""
        py_detector = PythonFrameworkDetector()

        framework = py_detector.detect_framework(None, "")
        assert framework == PythonFrameworkType.NONE

        no_fw_code = "def add(a, b):\n    return a + b"
        framework = py_detector.detect_framework(None, no_fw_code)
        assert framework == PythonFrameworkType.NONE

        endpoints = py_detector.extract_flask_routes(None, no_fw_code)
        assert endpoints == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
