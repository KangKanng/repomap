from minisweagent.agents.default import AgentConfig, DefaultAgent
from minisweagent.agents.interactive import InteractiveAgent, InteractiveAgentConfig
from minisweagent.models.utils.content_string import get_content_string
from pydantic import Field

from mini_swe_repomap.repomap import RepoMap
from mini_swe_repomap.settings import RepoMapConfig
from mini_swe_repomap.source import EnvironmentSource, RepoMapError


class RepoMapAgentConfig(AgentConfig):
    repomap: RepoMapConfig = Field(default_factory=RepoMapConfig)


class InteractiveRepoMapAgentConfig(InteractiveAgentConfig):
    repomap: RepoMapConfig = Field(default_factory=RepoMapConfig)


class RepoMapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repo_source = EnvironmentSource(self.env, self.config.repomap)
        self.repo_map: RepoMap | None = None
        self._repo_message: dict | None = None
        self._repo_map_attempted = False

    def run(self, task: str = "", **kwargs) -> dict:
        self._repo_message = None
        self._repo_map_attempted = False
        return super().run(task, **kwargs)

    def query(self) -> dict:
        settings = self.config.repomap
        current = next((i for i, message in enumerate(self.messages) if message is self._repo_message), None)
        if settings.map_tokens == 0:
            if current is not None:
                self.messages.pop(current)
            self._repo_message = None
        elif (settings.refresh == "once" and not self._repo_map_attempted) or (
            settings.refresh == "interval" and (current is None or self.n_calls % settings.refresh_interval == 0)
        ):
            self._repo_map_attempted = True
            context = self.extra_template_vars.get("task", "")
            if settings.mode == "budget" and settings.refresh == "interval" and settings.context_messages:
                recent = [
                    message
                    for message in self.messages
                    if message is not self._repo_message and message.get("role") != "system"
                ]
                context += (
                    "\n"
                    + "\n".join(get_content_string(message) for message in recent[-settings.context_messages :])[
                        -24_000:
                    ]
                )
            try:
                if self.repo_map is None:
                    self.repo_map = RepoMap(settings.tokenizer)
                content = self.repo_map.build(self.repo_source.read(), context, settings.map_tokens, mode=settings.mode)
                if settings.mode == "full" and content:
                    self.logger.info(
                        "Full repo map: %d paths, %d tokens (%s); no token selection.",
                        len(self.repo_source.sources),
                        self.repo_map.token_count(content),
                        settings.tokenizer,
                    )
                for warning in self.repo_source.warnings:
                    self.logger.warning("Repo map: %s", warning)
            except RepoMapError as error:
                if settings.strict:
                    raise
                self.logger.warning("Repo map unavailable: %s", error)
                content = ""
            if current is not None:
                self.messages.pop(current)
            self._repo_message = None
            if content:
                self._repo_message = self.model.format_message(role="user", content=content)
                self.messages.insert(2, self._repo_message)
        return super().query()


class RepoMapAgent(RepoMapMixin, DefaultAgent):
    """Non-interactive agent for the standard and SWE-bench runners."""

    def __init__(self, *args, config_class=RepoMapAgentConfig, **kwargs):
        super().__init__(*args, config_class=config_class, **kwargs)


class InteractiveRepoMapAgent(RepoMapMixin, InteractiveAgent):
    """The standard mini CLI agent, retaining its confirmation and human modes."""

    def __init__(self, *args, config_class=InteractiveRepoMapAgentConfig, **kwargs):
        super().__init__(*args, config_class=config_class, **kwargs)
