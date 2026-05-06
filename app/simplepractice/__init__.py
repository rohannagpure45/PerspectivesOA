"""SimplePractice JSON:API client + offline fixture backend."""

from app.simplepractice.client import SimplePracticeBackend, SimplePracticeClient
from app.simplepractice.fixture_backend import FixtureBackend
from app.simplepractice.jsonapi import Document, IncludedIndex, Resource

__all__ = [
    "Document",
    "FixtureBackend",
    "IncludedIndex",
    "Resource",
    "SimplePracticeBackend",
    "SimplePracticeClient",
]
