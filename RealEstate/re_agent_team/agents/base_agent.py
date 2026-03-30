"""エージェント基底クラス"""
import logging
from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """全エージェントの基底クラス"""

    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__
        self.logger = logging.getLogger(self.name)

    @abstractmethod
    def run(self, **kwargs):
        raise NotImplementedError
