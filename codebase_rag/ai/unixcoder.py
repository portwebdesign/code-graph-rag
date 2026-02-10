"""
This module provides an implementation of the UniXcoder model, a unified cross-modal
language model for code.

The implementation is adapted from the original Microsoft UniXcoder repository.
It uses the `transformers` library to load the pre-trained model and tokenizer.
The module includes the `UniXcoder` class, which wraps the model for tasks like
tokenization, embedding generation, and code generation, as well as a `Beam` class
to support beam search decoding.

Original source: https://github.com/microsoft/unixcoder
Copyright (c) Microsoft Corporation.
Licensed under the MIT license.
"""
# (H) Adapted from https://github.com/microsoft/unixcoder
# (H) Copyright (c) Microsoft Corporation.
# (H) Licensed under the MIT license.

import torch
from torch import nn
from transformers import RobertaConfig, RobertaModel, RobertaTokenizer

from codebase_rag.core import constants as cs


class UniXcoder(nn.Module):
    """
    A PyTorch module for the UniXcoder model.

    This class loads a pre-trained UniXcoder model and tokenizer and provides
    methods for tokenization, encoding (embedding generation), and decoding
    (code generation).

    Attributes:
        tokenizer (RobertaTokenizer): The tokenizer for the model.
        config (RobertaConfig): The configuration object for the model.
        model (RobertaModel): The underlying Transformer model.
        lm_head (nn.Linear): The language model head for generation tasks.
        lsm (nn.LogSoftmax): The log-softmax layer for output probabilities.
    """

    def __init__(self, model_name: str) -> None:
        """
        Initializes the UniXcoder model.

        Args:
            model_name (str): The name of the pre-trained model to load from
                              Hugging Face Hub (e.g., 'microsoft/unixcoder-base').
        """
        super().__init__()
        self.tokenizer: RobertaTokenizer = RobertaTokenizer.from_pretrained(model_name)
        self.config: RobertaConfig = RobertaConfig.from_pretrained(model_name)
        self.config.is_decoder = True
        self.model: RobertaModel = RobertaModel.from_pretrained(
            model_name, config=self.config
        )

        self.register_buffer(
            cs.UNIXCODER_BUFFER_BIAS,
            torch.tril(
                torch.ones(
                    (cs.UNIXCODER_MAX_CONTEXT, cs.UNIXCODER_MAX_CONTEXT),
                    dtype=torch.uint8,
                )
            ).view(1, cs.UNIXCODER_MAX_CONTEXT, cs.UNIXCODER_MAX_CONTEXT),
        )
        self.lm_head: nn.Linear = nn.Linear(
            self.config.hidden_size, self.config.vocab_size, bias=False
        )
        self.lm_head.weight = self.model.embeddings.word_embeddings.weight
        self.lsm: nn.LogSoftmax = nn.LogSoftmax(dim=-1)

        self.tokenizer.add_tokens([cs.UNIXCODER_MASK_TOKEN], special_tokens=True)

    def tokenize(
        self,
        inputs: list[str],
        mode: cs.UniXcoderMode = cs.UniXcoderMode.ENCODER_ONLY,
        max_length: int = 512,
        padding: bool = False,
    ) -> list[list[int]]:
        """
        Tokenizes a list of input strings according to the specified mode.

        Args:
            inputs (list[str]): The list of strings to tokenize.
            mode (cs.UniXcoderMode): The tokenization mode, which adds special
                                     tokens based on the task.
            max_length (int): The maximum sequence length.
            padding (bool): Whether to pad sequences to `max_length`.

        Returns:
            list[list[int]]: A list of token ID lists.
        """
        assert max_length < cs.UNIXCODER_MAX_CONTEXT

        tokenizer = self.tokenizer

        tokens_ids = []
        for x in inputs:
            tokens = tokenizer.tokenize(x)
            match mode:
                case cs.UniXcoderMode.ENCODER_ONLY:
                    tokens = tokens[: max_length - 4]
                    tokens = (
                        [tokenizer.cls_token, mode, tokenizer.sep_token]
                        + tokens
                        + [tokenizer.sep_token]
                    )
                case cs.UniXcoderMode.DECODER_ONLY:
                    tokens = tokens[-(max_length - 3) :]
                    tokens = [tokenizer.cls_token, mode, tokenizer.sep_token] + tokens
                case cs.UniXcoderMode.ENCODER_DECODER:
                    tokens = tokens[: max_length - 5]
                    tokens = (
                        [tokenizer.cls_token, mode, tokenizer.sep_token]
                        + tokens
                        + [tokenizer.sep_token]
                    )

            converted = tokenizer.convert_tokens_to_ids(tokens)
            tokens_id: list[int] = (
                converted if isinstance(converted, list) else [converted]
            )
            if padding:
                pad_id = self.config.pad_token_id
                assert pad_id is not None
                tokens_id += [pad_id] * (max_length - len(tokens_id))
            tokens_ids.append(tokens_id)
        return tokens_ids

    def decode(self, source_ids: torch.Tensor) -> list[list[str]]:
        """
        Decodes a batch of token IDs into strings.

        Args:
            source_ids (torch.Tensor): A tensor of token IDs.

        Returns:
            list[list[str]]: A list of decoded string lists.
        """
        predictions = []
        for x in source_ids:
            prediction = []
            for y in x:
                t = y.cpu().numpy()
                t = list(t)
                if 0 in t:
                    t = t[: t.index(0)]
                text = self.tokenizer.decode(t, clean_up_tokenization_spaces=False)
                prediction.append(text)
            predictions.append(prediction)
        return predictions

    def forward(self, source_ids: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Performs a forward pass to get token and sentence embeddings.

        Args:
            source_ids (torch.Tensor): A batch of input token IDs.

        Returns:
            tuple[torch.Tensor, torch.Tensor]: A tuple containing:
                - The token-level embeddings.
                - The sentence-level (mean-pooled) embeddings.
        """
        pad_id = self.config.pad_token_id
        assert pad_id is not None
        mask = source_ids.ne(pad_id)
        token_embeddings = self.model(
            source_ids, attention_mask=mask.unsqueeze(1) * mask.unsqueeze(2)
        )[0]
        sentence_embeddings = (token_embeddings * mask.unsqueeze(-1)).sum(1) / mask.sum(
            -1
        ).unsqueeze(-1)
        return token_embeddings, sentence_embeddings

    def generate(
        self,
        source_ids: torch.Tensor,
        decoder_only: bool = True,
        eos_id: int | None = None,
        beam_size: int = 5,
        max_length: int = 64,
    ) -> torch.Tensor:
        """
        Generates sequences using beam search.

        Args:
            source_ids (torch.Tensor): The input context token IDs.
            decoder_only (bool): Whether to use a causal mask for decoding.
            eos_id (int | None): The end-of-sequence token ID.
            beam_size (int): The number of beams to use in the search.
            max_length (int): The maximum length of the generated sequence.

        Returns:
            torch.Tensor: The generated token ID sequences.
        """
        # (H) self.bias is registered as buffer (Tensor) but typed as Module by ty
        bias: torch.Tensor = getattr(self, cs.UNIXCODER_BUFFER_BIAS)
        pad_id = self.config.pad_token_id
        assert pad_id is not None

        if decoder_only:
            mask = bias[:, : source_ids.size(-1), : source_ids.size(-1)]
        else:
            mask = source_ids.ne(pad_id)
            mask = mask.unsqueeze(1) * mask.unsqueeze(2)

        if eos_id is None:
            eos_id = self.config.eos_token_id
        assert eos_id is not None

        device = source_ids.device

        preds = []
        zero = torch.LongTensor(1).fill_(0).to(device)
        source_len = list(source_ids.ne(1).sum(-1).cpu().numpy())
        length = source_ids.size(-1)
        encoder_output = self.model(source_ids, attention_mask=mask)
        for i in range(source_ids.shape[0]):
            context = [
                [x[i : i + 1, :, : source_len[i]].repeat(beam_size, 1, 1, 1) for x in y]
                for y in encoder_output.past_key_values
            ]
            beam = Beam(beam_size, eos_id, device)
            input_ids = beam.getCurrentState().clone()
            context_ids = source_ids[i : i + 1, : source_len[i]].repeat(beam_size, 1)
            out = encoder_output.last_hidden_state[i : i + 1, : source_len[i]].repeat(
                beam_size, 1, 1
            )
            for _ in range(max_length):
                if beam.done():
                    break
                if _ == 0:
                    hidden_states = out[:, -1, :]
                    out = self.lsm(self.lm_head(hidden_states)).data
                    beam.advance(out)
                    input_ids.data.copy_(
                        input_ids.data.index_select(0, beam.getCurrentOrigin())
                    )
                    input_ids = beam.getCurrentState().clone()
                else:
                    length = context_ids.size(-1) + input_ids.size(-1)
                    out = self.model(
                        input_ids,
                        attention_mask=bias[:, context_ids.size(-1) : length, :length],
                        past_key_values=context,
                    ).last_hidden_state
                    hidden_states = out[:, -1, :]
                    out = self.lsm(self.lm_head(hidden_states)).data
                    beam.advance(out)
                    input_ids.data.copy_(
                        input_ids.data.index_select(0, beam.getCurrentOrigin())
                    )
                    input_ids = torch.cat(
                        (input_ids, beam.getCurrentState().clone()), -1
                    )
            hyp = beam.getHyp(beam.getFinal())
            pred = beam.buildTargetTokens(hyp)[:beam_size]
            pred = [
                torch.cat(
                    [x.view(-1) for x in p] + [zero] * (max_length - len(p))
                ).view(1, -1)
                for p in pred
            ]
            preds.append(torch.cat(pred, 0).unsqueeze(0))

        preds = torch.cat(preds, 0)

        return preds


class Beam:
    """
    Implements a beam search algorithm for sequence generation.

    This class manages the state of the beam search, including scores,
    previous states, and generated tokens. It is used by the `generate`
    method of the `UniXcoder` class.
    """

    def __init__(self, size: int, eos: int, device: torch.device) -> None:
        """
        Initializes the Beam search object.

        Args:
            size (int): The beam size.
            eos (int): The end-of-sequence token ID.
            device (torch.device): The device to perform computations on.
        """
        self.size = size
        self.device = device
        self.scores: torch.Tensor = torch.FloatTensor(size).zero_().to(device)
        self.prevKs: list[torch.Tensor] = []
        self.nextYs: list[torch.Tensor] = [torch.LongTensor(size).fill_(0).to(device)]
        self._eos = eos
        self.eosTop = False
        self.finished: list[tuple[torch.Tensor, int, int]] = []

    def getCurrentState(self) -> torch.Tensor:
        """Gets the last generated tokens for all beams."""
        batch = self.nextYs[-1].view(-1, 1)
        return batch

    def getCurrentOrigin(self) -> torch.Tensor:
        """Gets the beam indices of the previous step."""
        return self.prevKs[-1]

    def advance(self, wordLk: torch.Tensor) -> None:
        """
        Advances the beam search one step.

        Args:
            wordLk (torch.Tensor): The log-probabilities of the next words.
        """
        numWords = wordLk.size(1)

        if len(self.prevKs) > 0:
            beamLk = wordLk + self.scores.unsqueeze(1).expand_as(wordLk)

            for i in range(self.nextYs[-1].size(0)):
                if self.nextYs[-1][i] == self._eos:
                    beamLk[i] = -1e20
        else:
            beamLk = wordLk[0]
        flatBeamLk = beamLk.view(-1)
        bestScores, bestScoresId = flatBeamLk.topk(self.size, 0, True, True)

        self.scores = bestScores

        prevK = torch.div(bestScoresId, numWords, rounding_mode="floor")
        self.prevKs.append(prevK)
        self.nextYs.append(bestScoresId - prevK * numWords)

        for i in range(self.nextYs[-1].size(0)):
            if self.nextYs[-1][i] == self._eos:
                s = self.scores[i]
                self.finished.append((s, len(self.nextYs) - 1, i))

        if self.nextYs[-1][0] == self._eos:
            self.eosTop = True

    def done(self) -> bool:
        """Checks if the beam search has finished."""
        return self.eosTop and len(self.finished) >= self.size

    def getFinal(self) -> list[tuple[torch.Tensor, int, int]]:
        """Gets the final finished hypotheses."""
        if len(self.finished) == 0:
            self.finished.append((self.scores[0], len(self.nextYs) - 1, 0))
        self.finished.sort(key=lambda a: -a[0])
        if len(self.finished) != self.size:
            unfinished = [
                (self.scores[i], len(self.nextYs) - 1, i)
                for i in range(self.nextYs[-1].size(0))
                if self.nextYs[-1][i] != self._eos
            ]
            unfinished.sort(key=lambda a: -a[0])
            self.finished += unfinished[: self.size - len(self.finished)]
        return self.finished[: self.size]

    def getHyp(
        self, beam_res: list[tuple[torch.Tensor, int, int]]
    ) -> list[list[torch.Tensor]]:
        """
        Traces back through the beam history to reconstruct the hypotheses.

        Args:
            beam_res (list): The final beam results.

        Returns:
            list[list[torch.Tensor]]: A list of hypotheses, where each hypothesis
                                      is a list of token tensors.
        """
        hyps: list[list[torch.Tensor]] = []
        for _, timestep, k in beam_res:
            hyp: list[torch.Tensor] = []
            for j in range(len(self.prevKs[:timestep]) - 1, -1, -1):
                hyp.append(self.nextYs[j + 1][k])
                k = self.prevKs[j][k]
            hyps.append(hyp[::-1])
        return hyps

    def buildTargetTokens(
        self, preds: list[list[torch.Tensor]]
    ) -> list[list[torch.Tensor]]:
        """
        Converts hypotheses into final token sequences, stopping at the EOS token.

        Args:
            preds (list[list[torch.Tensor]]): The hypotheses from `getHyp`.

        Returns:
            list[list[torch.Tensor]]: The final list of token sequences.
        """
        sentence: list[list[torch.Tensor]] = []
        for pred in preds:
            tokens: list[torch.Tensor] = []
            for tok in pred:
                if tok == self._eos:
                    break
                tokens.append(tok)
            sentence.append(tokens)
        return sentence
