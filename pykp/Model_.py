# -*- coding: utf-8 -*-
"""
Python File Template 
"""
import torch
import torch.nn as nn
import torch.nn.functional as func
from torch.autograd import Variable
import numpy as np

__author__ = "Rui Meng"
__email__ = "rui.meng@pitt.edu"


class Attention(nn.Module):
    def __init__(self, hidden_size, method='concat'):
        super(Attention, self).__init__()

        self.method = method
        self.hidden_size = hidden_size

        if self.method == 'general':
            self.attn = nn.Linear(self.hidden_size, hidden_size)

        elif self.method == 'concat':
            self.attn = nn.Linear(self.hidden_size * 2, hidden_size)
            self.other = nn.Parameter(torch.FloatTensor(1, hidden_size))

    def forward(self, hidden, encoder_outputs):
        seq_len = len(encoder_outputs)

        # Create variable to store attention energies
        attn_energies = Variable(torch.zeros(seq_len))  # B x 1 x S
        if torch.cuda.is_available(): attn_energies = attn_energies.cuda()

        # Calculate energies for each encoder output
        for i in range(seq_len):
            attn_energies[i] = self.score(hidden, encoder_outputs[i])

        # Normalize energies to weights in range 0 to 1, resize to 1 x 1 x seq_len
        return torch.nn.functional.softmax(attn_energies).unsqueeze(0).unsqueeze(0)

    def score(self, hidden, encoder_output):
        if self.method == 'dot':
            energy = hidden.dot(encoder_output)
            return energy

        elif self.method == 'general':
            energy = self.attn(encoder_output)
            energy = hidden.dot(energy)
            return energy

        elif self.method == 'concat':
            energy = self.attn(torch.cat((hidden, encoder_output), 1))
            energy = self.other.dot(energy)
            return energy

class SoftConcatAttention(nn.Module):
    def __init__(self, enc_dim, trg_dim):
        super(SoftConcatAttention, self).__init__()
        self.linear_in  = nn.Linear(trg_dim, trg_dim, bias=False)
        self.linear_ctx = nn.Linear(enc_dim, trg_dim)

        self.attn = nn.Linear(enc_dim + trg_dim, trg_dim)
        self.v = nn.Parameter(torch.FloatTensor(1, trg_dim))
        self.softmax = nn.Softmax()
        self.linear_out = nn.Linear(trg_dim * 2, trg_dim, bias=False)
        self.tanh = nn.Tanh()
        self.mask = None
        self.method = 'concat'

    def score(self, hidden, encoder_output):
        if self.method == 'dot':
            energy = hidden.dot(encoder_output)
            return energy

        elif self.method == 'general':
            energy = self.attn(encoder_output)
            energy = hidden.dot(energy)
            return energy

        elif self.method == 'concat':
            energy = self.attn(torch.cat((hidden, encoder_output), 1))
            energy = torch.matmul(energy, self.v.t())
            return energy

    def forward_(self, hidden, context):
        """
        Original forward for DotAttention, it doesn't work if the dim of encoder and decoder are not same
        input and context must be in same dim: return Softmax(hidden.dot([c for c in context]))
        input: batch x hidden_dim
        context: batch x source_len x hidden_dim
        """
        target = self.linear_in(hidden).unsqueeze(2)  # batch x hidden_dim x 1

        # Get attention, size=(batch_size, source_len, 1) -> (batch_size, source_len)
        attn = torch.bmm(context, target).squeeze(2)  # batch x source_len
        attn = self.softmax(attn)
        attn3 = attn.view(attn.size(0), 1, attn.size(1))  # batch_size x 1 x source_len

        # Get the weighted context vector
        weighted_context = torch.bmm(attn3, context).squeeze(1)  # batch_size x hidden_dim

        # Update h by tanh(torch.cat(weighted_context, input))
        h_tilde = torch.cat((weighted_context, hidden), 1) # batch_size * (src_hidden_dim + trg_hidden_dim)
        h_tilde = self.tanh(self.linear_out(h_tilde)) # batch_size * trg_hidden_dim

        return h_tilde, attn

    def forward(self, hidden, encoder_outputs):
        '''
        Compute the attention and h_tilde
        :param hidden:
        :param encoder_outputs:
        :return:
        '''

        # Calculate energies for each encoder output
        hidden = hidden.unsqueeze(2)
        attn_energies = torch.bmm(encoder_outputs, hidden)
        # Normalize energies to weights in range 0 to 1, resize to 1 x 1 x seq_len
        attn = torch.nn.functional.softmax(attn_energies)

        # get the weighted context
        weighted_context = torch.bmm(encoder_outputs.permute(0, 2, 1), attn).squeeze()  # batch x hidden_dim

        # update the hidden by = tanh(W_c[c_t, h_t])
        hidden = hidden.squeeze()
        h_tilde = torch.cat((weighted_context, hidden), 1)
        h_tilde = self.tanh(self.linear_out(h_tilde))

        return h_tilde, attn

class LSTMAttentionDot(nn.Module):
    """
    A long short-term memory (LSTM) cell with attention.
    Return the hidden output (h_tilde) of each time step, same as the normal LSTM layer. Will get the decoder_logit by softmax in the outer loop
    Current is Teacher Forcing Learning: feed the ground-truth target as the next input

    """

    def __init__(self, input_size, src_hidden_size, trg_hidden_size):
        """Initialize params."""
        super(LSTMAttentionDot, self).__init__()
        self.input_size = input_size
        self.hidden_size = trg_hidden_size
        self.num_layers = 1

        self.input_weights = nn.Linear(input_size, 4 * trg_hidden_size)
        self.hidden_weights = nn.Linear(trg_hidden_size, 4 * trg_hidden_size)

        self.attention_layer = SoftConcatAttention(src_hidden_size, trg_hidden_size)

    def forward(self, input, hidden, ctx, ctx_mask=None):
        """
        Propogate input through the network.
            input: embedding of targets (ground-truth), batch must come first (batch_size, seq_len, hidden_size * num_directions)
            hidden = (h0, c0): hidden (converted from the end hidden state of encoder) and cell (end cell state of encoder) vectors, (seq_len, batch_size, hidden_size * num_directions)
            ctx: context vectors for attention: hidden vectors of encoder for all the time steps(seq_len, batch_size, hidden_size * num_directions)
            ctx_mask
        """
        def recurrence(x, last_hidden):
            """
            Implement the recurrent procedure of LSTM manually (not necessary)
            """
            # hx, cx are the hidden states of time t-1
            hx, cx = last_hidden  # (seq_len, batch_size, hidden_size * num_directions)

            # gate values = W_x * x + W_h * h (batch_size, 4 * trg_hidden_size)
            gates = self.input_weights(x) + self.hidden_weights(hx)
            ingate, forgetgate, cellgate, outgate = gates.chunk(4, 1)

            # compute each gate, all are in (batch_size, trg_hidden_size)
            ingate = func.sigmoid(ingate)
            forgetgate = func.sigmoid(forgetgate)
            cellgate = func.tanh(cellgate)
            outgate = func.sigmoid(outgate)

            # get the cell and hidden state of time t (batch_size, trg_hidden_size)
            ct = (forgetgate * cx) + (ingate * cellgate)
            ht = outgate * func.tanh(ct)

            # update ht with attention
            h_tilde, alpha = self.attention_layer(ht, ctx.transpose(0, 1))

            return h_tilde, (ht, ct)

        '''
        if input is not None, means it's training (teacher forcing)
            otherwise it's predicting (as well later we can add training without teacher forcing)
        '''
        if input:
            # reshape the targets to be time step first
            input = input.permute(1, 0, 2)
            output = []
            # iterate each time step of target sequences and generate decode outputs
            for i in range(input.size(0)):
                # Get the h_tilde for output and new hidden for next time step, x=input[i], last_hidden=hidden
                h_tilde, hidden = recurrence(input[i], hidden)
                # compute the output with h_tilde: p_x = Softmax(W_s * h_tilde)
                output.append(h_tilde)
        else:
            # reshape the targets to be time step first
            output = []
            # iterate each time step of target sequences and generate decode outputs
            for i in range(input.size(0)):
                # Get the h_tilde for output and new hidden for next time step, x=input[i], last_hidden=hidden
                h_tilde, hidden = recurrence(input[i], hidden)
                # compute the output with h_tilde: p_x = Softmax(W_s * h_tilde)
                output.append(h_tilde)


        # convert output into the right shape
        output = torch.cat(output, 0).view(input.size(0), *output[0].size())
        # make batch first
        output = output.transpose(0, 1)

        # return the outputs of each time step and the hidden vector of last time step
        return output, hidden


class Seq2SeqLSTMAttention(nn.Module):
    """Container module with an encoder, deocder, embeddings."""

    def __init__(
        self,
        emb_dim,
        vocab_size,
        src_hidden_dim,
        trg_hidden_dim,
        ctx_hidden_dim,
        attention_mode,
        batch_size,
        pad_token_src,
        pad_token_trg,
        bidirectional=True,
        nlayers_src=2,
        nlayers_trg=2,
        dropout=0.,
    ):
        """Initialize model."""
        super(Seq2SeqLSTMAttention, self).__init__()
        self.vocab_size         = vocab_size
        self.emb_dim            = emb_dim
        self.src_hidden_dim     = src_hidden_dim
        self.trg_hidden_dim     = trg_hidden_dim
        self.ctx_hidden_dim     = ctx_hidden_dim
        self.attention_mode     = attention_mode
        self.batch_size         = batch_size
        self.bidirectional      = bidirectional
        self.nlayers_src        = nlayers_src
        self.dropout            = dropout
        self.num_directions     = 2 if bidirectional else 1
        self.pad_token_src      = pad_token_src
        self.pad_token_trg      = pad_token_trg

        self.embedding = nn.Embedding(
            vocab_size,
            emb_dim,
            self.pad_token_src
        )

        self.encoder = nn.LSTM(
            input_size      = emb_dim,
            hidden_size     = self.src_hidden_dim,
            num_layers      = nlayers_src,
            bidirectional   = bidirectional,
            batch_first     = True,
            dropout         = self.dropout
        )

        self.decoder = LSTMAttentionDot(
            emb_dim,
            self.src_hidden_dim * self.num_directions,
            trg_hidden_dim
        )

        self.encoder2decoder_hidden = nn.Linear(
            self.src_hidden_dim * self.num_directions,
            trg_hidden_dim
        )

        self.encoder2decoder_cell = nn.Linear(
            self.src_hidden_dim * self.num_directions,
            trg_hidden_dim
        )

        self.decoder2vocab = nn.Linear(trg_hidden_dim, vocab_size)

        self.init_weights()

    def init_weights(self):
        """Initialize weights."""
        initrange = 0.1
        self.embedding.weight.data.uniform_(-initrange, initrange)
        self.encoder2decoder_hidden.bias.data.fill_(0)
        self.encoder2decoder_cell.bias.data.fill_(0)
        self.decoder2vocab.bias.data.fill_(0)

    def get_state(self, input):
        """Get cell states and hidden states."""
        batch_size = input.size(0) \
            if self.encoder.batch_first else input.size(1)

        h0_encoder = Variable(torch.zeros(
            self.encoder.num_layers * self.num_directions,
            batch_size,
            self.src_hidden_dim
        ), requires_grad=False)

        c0_encoder = Variable(torch.zeros(
            self.encoder.num_layers * self.num_directions,
            batch_size,
            self.src_hidden_dim
        ), requires_grad=False)

        if torch.cuda.is_available():
            return h0_encoder.cuda(), c0_encoder.cuda()

        return h0_encoder, c0_encoder

    def forward(self, input_src, input_trg, trg_mask=None, ctx_mask=None, ):
        src_h, (src_h_t, src_c_t) = self.encode(input_src)
        decoder_logit = self.decode(input=input_trg, enc_context=src_h, enc_hidden=(src_h_t, src_c_t), trg_mask=trg_mask, ctx_mask=ctx_mask)
        return decoder_logit

    def forward_(self, input_src, input_trg, trg_mask=None, ctx_mask=None, ):
        """Propogate input through the network."""
        src_emb = self.embedding(input_src)
        trg_emb = self.embedding(input_trg)

        # initial encoder state, two zero-matrix as h and c at time=0
        self.h0_encoder, self.c0_encoder = self.get_state(input_src) # (self.encoder.num_layers * self.num_directions, batch_size, self.src_hidden_dim)

        # src_h (batch_size, seq_len, hidden_size * num_directions): outputs (h_t) of all the time steps
        # src_h_t, src_c_t (num_layers * num_directions, batch, hidden_size): hidden and cell state at last time step
        src_h, (src_h_t, src_c_t) = self.encoder(
            src_emb, (self.h0_encoder, self.c0_encoder)
        )

        # concatenate to (batch_size, hidden_size * num_directions)
        if self.bidirectional:
            h_t = torch.cat((src_h_t[-1], src_h_t[-2]), 1)
            c_t = torch.cat((src_c_t[-1], src_c_t[-2]), 1)
        else:
            h_t = src_h_t[-1]
            c_t = src_c_t[-1]
        '''
        Initial decoder state h0 (batch_size, trg_hidden_size), converted from h_t of encoder (batch_size, src_hidden_size * num_directions) through a linear layer
            No transformation for cell state c_t. Pass directly to decoder.
            Nov. 11st: update: change to pass c_t as well
            People also do that directly feed the end hidden state of encoder and initialize cell state as zeros
        '''
        decoder_init_hidden = nn.Tanh()(self.encoder2decoder_hidden(h_t))
        decoder_init_cell   = nn.Tanh()(self.encoder2decoder_cell(c_t))

        # context vector ctx0 = outputs of encoder(seq_len, batch_size, hidden_size * num_directions)
        ctx = src_h.transpose(0, 1)

        # output, (hidden, cell)
        trg_h, (_, _) = self.decoder(
            trg_emb,
            (decoder_init_hidden, decoder_init_cell),
            ctx,
            ctx_mask
        )
        # flatten the trg_output, feed into the readout layer, and get the decoder_logit
        # (batch_size, trg_length, trg_hidden_size) -> (batch_size * trg_length, trg_hidden_size)
        trg_h_reshape = trg_h.contiguous().view(
            trg_h.size()[0] * trg_h.size()[1],
            trg_h.size()[2]
        )

        # (batch_size * trg_length, vocab_size)
        decoder_logit = self.decoder2vocab(trg_h_reshape)
        # (batch_size * trg_length, vocab_size) -> (batch_size, trg_length, vocab_size)
        decoder_logit = decoder_logit.view(
            trg_h.size()[0],
            trg_h.size()[1],
            decoder_logit.size()[1]
        )
        return decoder_logit

    def logit2prob(self, logits):
        """Return probability distribution over words."""
        logits_reshape = logits.view(-1, self.vocab_size)
        word_probs = func.softmax(logits_reshape)
        word_probs = word_probs.view(
            logits.size()[0], logits.size()[1], logits.size()[2]
        )
        return word_probs

    def encode(self, input_src):
        """Propogate input through the network."""
        src_emb = self.embedding(input_src)

        # initial encoder state, two zero-matrix as h and c at time=0
        self.h0_encoder, self.c0_encoder = self.get_state(input_src) # (self.encoder.num_layers * self.num_directions, batch_size, self.src_hidden_dim)

        # src_h (batch_size, seq_len, hidden_size * num_directions): outputs (h_t) of all the time steps
        # src_h_t, src_c_t (num_layers * num_directions, batch, hidden_size): hidden and cell state at last time step
        src_h, (src_h_t, src_c_t) = self.encoder(
            src_emb, (self.h0_encoder, self.c0_encoder)
        )

        # concatenate to (batch_size, hidden_size * num_directions)
        if self.bidirectional:
            h_t = torch.cat((src_h_t[-1], src_h_t[-2]), 1)
            c_t = torch.cat((src_c_t[-1], src_c_t[-2]), 1)
        else:
            h_t = src_h_t[-1]
            c_t = src_c_t[-1]

        return src_h, (h_t, c_t)

    def decode(self, input, enc_context, enc_hidden, trg_mask, ctx_mask):
        '''
        Initial decoder state h0 (batch_size, trg_hidden_size), converted from h_t of encoder (batch_size, src_hidden_size * num_directions) through a linear layer
            No transformation for cell state c_t. Pass directly to decoder.
            Nov. 11st: update: change to pass c_t as well
            People also do that directly feed the end hidden state of encoder and initialize cell state as zeros
        '''
        h_t, c_t = enc_hidden
        trg_emb = self.embedding(input)
        decoder_init_hidden = nn.Tanh()(self.encoder2decoder_hidden(h_t))
        decoder_init_cell   = nn.Tanh()(self.encoder2decoder_cell(c_t))

        # context vector ctx0 = outputs of encoder(seq_len, batch_size, hidden_size * num_directions)
        ctx = enc_context.transpose(0, 1)

        # output, (hidden, cell)
        trg_h, (_, _) = self.decoder(
            trg_emb,
            (decoder_init_hidden, decoder_init_cell),
            ctx,
            ctx_mask
        )
        # flatten the trg_output, feed into the readout layer, and get the decoder_logit
        # (batch_size, trg_length, trg_hidden_size) -> (batch_size * trg_length, trg_hidden_size)
        trg_h_reshape = trg_h.contiguous().view(
            trg_h.size()[0] * trg_h.size()[1],
            trg_h.size()[2]
        )

        # (batch_size * trg_length, vocab_size)
        decoder_logit = self.decoder2vocab(trg_h_reshape)
        # (batch_size * trg_length, vocab_size) -> (batch_size, trg_length, vocab_size)
        decoder_logit = decoder_logit.view(
            trg_h.size()[0],
            trg_h.size()[1],
            decoder_logit.size()[1]
        )
        return decoder_logit