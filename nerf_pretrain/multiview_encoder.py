import torch
import torch.nn as nn


def tie_weights(src, trg):
    assert type(src) == type(trg)
    trg.weight = src.weight
    trg.bias = src.bias


# for 84 x 84 inputs
OUT_DIM = {2: 39, 4: 35, 6: 31}
# for 64 x 64 inputs
OUT_DIM_64 = {2: 29, 4: 25, 6: 21}
# for 128 x 128 inputs
OUT_DIM_128 = {2: 29, 4: 57, 6: 21}


class PixelEncoder(nn.Module):
    """Convolutional encoder of pixels observations."""

    def __init__(
        self,
        obs_shape=(3, 128, 128),
        feature_dim=63,
        num_layers=4,
        num_filters=32,
        output_logits=True,
    ):
        super().__init__()

        assert len(obs_shape) == 3
        self.obs_shape = obs_shape
        self.feature_dim = feature_dim
        self.num_layers = num_layers

        if obs_shape[-1] == 64:
            out_dim = OUT_DIM_64[num_layers]
        elif obs_shape[-1] == 128:
            out_dim = OUT_DIM_128[num_layers]
        else:
            out_dim = OUT_DIM[num_layers]

        self.convs = nn.ModuleList([nn.Conv2d(obs_shape[0], num_filters, 3, stride=2)])
        for i in range(num_layers - 1):
            self.convs.append(
                nn.Conv2d(num_filters, num_filters, kernel_size=3, stride=1)
            )

        # self.fc = nn.Linear(num_filters * out_dim * out_dim, self.feature_dim)
        # self.fc = nn.Linear(1737248, self.feature_dim)
        self.fc = nn.Linear(
            in_features=num_filters
            * out_dim
            * out_dim,  # kutay: in_features=num_filters * out_dim * out_dim * 3,
            out_features=self.feature_dim,
        )

        self.ln = nn.LayerNorm(self.feature_dim)

        self.outputs = dict()

        self.output_logits = output_logits

        self.mlp1 = nn.Linear((self.feature_dim + 16), 63)
        self.mlp2 = nn.Linear(63, 63)

    def reparameterize(self, mu, logstd):
        std = torch.exp(logstd)
        eps = torch.randn_like(std)
        return mu + eps * std

    def forward_conv(self, obs):
        # obs = obs / 255.

        self.outputs["obs"] = obs

        conv = torch.relu(self.convs[0](obs))
        self.outputs["conv1"] = conv

        for i in range(1, self.num_layers):
            conv = torch.relu(self.convs[i](conv))
            self.outputs["conv%s" % (i + 1)] = conv

        h = conv.reshape(conv.size(0), -1)

        return h

    def forward(self, obs, obs_pose, detach=False):
        h = self.forward_conv(obs)

        h = self.fc(h)

        h = torch.cat([h, obs_pose], dim=1)
        h = self.mlp1(h)
        h = torch.mean(h, dim=0, keepdim=True)
        h_fc = self.mlp2(h)

        if detach:
            h = h.detach()

        # h_fc = self.fc(h)
        self.outputs["fc"] = h_fc

        h_norm = self.ln(h_fc)

        self.outputs["ln"] = h_norm

        if self.output_logits:
            out = h_norm
        else:
            out = torch.tanh(h_norm)
            self.outputs["tanh"] = out

        return out

    def copy_conv_weights_from(self, source):
        """Tie convolutional layers"""
        # only tie conv layers
        for i in range(self.num_layers):
            tie_weights(src=source.convs[i], trg=self.convs[i])

    def log(self, L, step, log_freq):
        if step % log_freq != 0:
            return

        for k, v in self.outputs.items():
            L.log_histogram("train_encoder/%s_hist" % k, v, step)
            if len(v.shape) > 2:
                L.log_image("train_encoder/%s_img" % k, v[0], step)

        for i in range(self.num_layers):
            L.log_param("train_encoder/conv%s" % (i + 1), self.convs[i], step)
        L.log_param("train_encoder/fc", self.fc, step)
        L.log_param("train_encoder/ln", self.ln, step)
