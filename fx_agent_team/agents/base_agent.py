"""Base agent class."""

from abc import ABC, abstractmethod
import logging


class BaseAgent(ABC):
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)

    @abstractmethod
    def run(self, **kwargs):
        ...
