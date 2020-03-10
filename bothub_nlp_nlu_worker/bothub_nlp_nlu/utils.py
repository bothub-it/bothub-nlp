import io
import logging
import bothub_backend
import contextvars
from tempfile import mkdtemp
from decouple import config
from rasa.nlu import components
from rasa.nlu.config import RasaNLUModelConfig

from rasa.nlu.model import Interpreter
from .persistor import BothubPersistor


class BothubInterpreter(Interpreter):
    @staticmethod
    def default_output_attributes():  # pragma: no cover
        return {
            "intent": {"name": None, "confidence": 0.0},
            "entities": [],
            "labels_as_entity": [],
        }


def backend():
    return bothub_backend.get_backend(
        "bothub_backend.bothub.BothubBackend",
        config("BOTHUB_ENGINE_URL", default="https://api.bothub.it"),
    )


def get_rasa_nlu_config_from_update(update):  # pragma: no cover
    pipeline = []
    if update.get("algorithm") == update.get("ALGORITHM_STATISTICAL_MODEL"):
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "optimized_spacy_nlp_with_labels.SpacyNLP"
            }
        )
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "tokenizer_spacy_with_labels.SpacyTokenizer"
            }
        )
        pipeline.append({"name": "RegexFeaturizer"})
        pipeline.append({"name": "SpacyFeaturizer"})
        pipeline.append({"name": "CRFEntityExtractor"})
        # spacy named entity recognition
        if update.get("use_name_entities"):
            pipeline.append({"name": "SpacyEntityExtractor"})
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "crf_label_as_entity_extractor.CRFLabelAsEntityExtractor"
            }
        )
        pipeline.append({"name": "SklearnIntentClassifier"})
    else:
        use_spacy = update.get("algorithm") == update.get(
            "ALGORITHM_NEURAL_NETWORK_EXTERNAL"
        )
        # load spacy
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "optimized_spacy_nlp_with_labels.SpacyNLP"
            }
        )
        # tokenizer
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "tokenizer_spacy_with_labels.SpacyTokenizer"
            }
        )
        # featurizer
        if use_spacy:
            pipeline.append({"name": "SpacyFeaturizer"})
        else:
            if update.get("use_analyze_char"):
                pipeline.append(
                    {
                        "name": "CountVectorsFeaturizer",
                        "analyzer": "char",
                        "min_ngram": 3,
                        "max_ngram": 3,
                        "token_pattern": "(?u)\\b\\w+\\b",
                    }
                )
            else:
                pipeline.append(
                    {
                        "name": "bothub_nlp_nlu.pipeline_components."
                        "count_vectors_featurizer_no_lemmatize.CountVectorsFeaturizerCustom",
                        "token_pattern": "(?u)\\b\\w+\\b",
                    }
                )
        # intent classifier
        pipeline.append(
            {
                "name": "EmbeddingIntentClassifier",
                "similarity_type": "inner"
                if update.get("use_competing_intents")
                else "cosine",
            }
        )

        # entity extractor
        pipeline.append({"name": "CRFEntityExtractor"})
        # spacy named entity recognition
        if update.get("use_name_entities"):
            pipeline.append({"name": "SpacyEntityExtractor"})
        # label extractor
        pipeline.append(
            {
                "name": "bothub_nlp_nlu.pipeline_components."
                "crf_label_as_entity_extractor.CRFLabelAsEntityExtractor"
            }
        )
    return RasaNLUModelConfig(
        {"language": update.get("language"), "pipeline": pipeline}
    )


class UpdateInterpreters:
    interpreters = {}

    def get(self, repository_version, repository_authorization, use_cache=True):
        update_request = backend().request_backend_parse_nlu(
            repository_version, repository_authorization
        )

        repository_name = (
            f"{update_request.get('version_id')}_"
            f"{update_request.get('total_training_end')}_"
            f"{update_request.get('language')}"
        )

        interpreter = self.interpreters.get(repository_name)

        if interpreter and use_cache:
            return interpreter
        persistor = BothubPersistor(repository_version, repository_authorization)
        model_directory = mkdtemp()
        persistor.retrieve(str(update_request.get("repository_uuid")), model_directory)
        print(update_request)
        self.interpreters[repository_name] = BothubInterpreter.load(
            model_directory, components.ComponentBuilder(use_cache=False)
        )
        return self.get(repository_version, repository_authorization)


class PokeLoggingHandler(logging.StreamHandler):
    def __init__(self, pl, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pl = pl

    def emit(self, record):
        if self.pl.cxt.get(default=None) is self.pl:
            super().emit(record)


class PokeLogging:
    def __init__(self, loggingLevel=logging.DEBUG):
        self.loggingLevel = loggingLevel

    def __enter__(self):
        self.cxt = contextvars.ContextVar(self.__class__.__name__)
        self.cxt.set(self)
        logging.captureWarnings(True)
        self.logger = logging.getLogger()
        self.logger.setLevel(self.loggingLevel)
        self.stream = io.StringIO()
        self.handler = PokeLoggingHandler(self, self.stream)
        self.formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        self.handler.setLevel(self.loggingLevel)
        self.handler.setFormatter(self.formatter)
        self.logger.addHandler(self.handler)
        return self.stream

    def __exit__(self, *args):
        self.logger.removeHandler(self.logger)


update_interpreters = UpdateInterpreters()


def get_examples_request(update_id, repository_authorization):  # pragma: no cover
    start_examples = backend().request_backend_get_examples(
        update_id, False, None, repository_authorization
    )

    examples = start_examples.get("results")

    page = start_examples.get("next")

    if page:
        while True:
            request_examples_page = backend().request_backend_get_examples(
                update_id, True, page, repository_authorization
            )

            examples += request_examples_page.get("results")

            if request_examples_page.get("next") is None:
                break

            page = request_examples_page.get("next")

    return examples


def get_examples_label_request(update_id, repository_authorization):  # pragma: no cover
    start_examples = backend().request_backend_get_examples_labels(
        update_id, False, None, repository_authorization
    )

    examples_label = start_examples.get("results")

    page = start_examples.get("next")

    if page:
        while True:
            request_examples_page = backend().request_backend_get_examples_labels(
                update_id, True, page, repository_authorization
            )

            examples_label += request_examples_page.get("results")

            if request_examples_page.get("next") is None:
                break

            page = request_examples_page.get("next")

    return examples_label
