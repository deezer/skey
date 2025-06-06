from typing import List

from einops import rearrange
from torch import nn

from skey.convnext import ConvNeXtBlock, TimeDownsamplingBlock


class OctavePool(nn.Module):
    r"""
    Average log-frequency axis across octaves, thus producing a chromagram.

    Args:
        bins_per_octave (int): Number of frequency bins per octave.

    Forward:
        Args:
            x (Tensor): Input tensor of shape (batch_size, channel, H, W),
                        where H is the height representing frequency bins.

        Returns:
            Tensor: Output tensor of shape (batch_size, channel, bins_per_octave, W),
                    where the frequency axis is averaged across octaves.
    """

    def __init__(self, bins_per_octave: int):
        super().__init__()
        self.bins_per_octave = bins_per_octave

    def forward(self, x):
        # x: (batch_size, channel, H, W)
        x = rearrange(x, "B C (j k) W -> B C k j W", k=self.bins_per_octave)
        x = x.mean(dim=3)
        return x


class ChromaNet(nn.Module):
    """
    ChromaNet is a neural network proposed in paper STONE (ISMIR 2024).

    Args:
        n_bins (int): Number of frequency bins in the input.
        n_harmonics (int): Number of harmonics in the input.
        out_channels (List[int]): List of output channels for each layer.
        kernels (List[int]): List of kernel sizes for each layer.
        temperature (float): Temperature parameter for softmax scaling.

    Forward:
        Args:
            x (Tensor): Input tensor of shape (batch_size, n_harmonics, n_bins, time_steps).

        Returns:
            Tensor: Output tensor of shape (batch_size, 24), representing the processed chroma features.
    """

    def __init__(
        self,
        n_bins: int,
        n_harmonics: int,
        out_channels: List[int],
        kernels: List[int],
        temperature: float,
    ):
        super().__init__()
        assert len(kernels) == len(out_channels)
        self.n_harmonics = n_harmonics
        self.n_bins = n_bins
        in_channel = self.n_harmonics
        self.out_channels = out_channels
        self.kernels = kernels
        self.temperature = temperature
        self.drop_path = 0.1
        time_downsampling_blocks = []
        convnext_blocks = []
        for i, out_channel in enumerate(self.out_channels):
            time_downsampling_block = TimeDownsamplingBlock(in_channel, out_channel)
            kernel = self.kernels[i]
            convnext_block = ConvNeXtBlock(
                out_channel,
                out_channel,
                kernel_size=kernel,
                padding=kernel // 2,
                drop_path=self.drop_path,
            )
            time_downsampling_blocks.append(time_downsampling_block)
            convnext_blocks.append(convnext_block)
            in_channel = out_channel
        self.convnext_blocks = nn.ModuleList(convnext_blocks)
        self.time_downsampling_blocks = nn.ModuleList(time_downsampling_blocks)
        self.octave_pool = OctavePool(12)
        self.global_average_pool = nn.AdaptiveAvgPool2d((12, 1))
        self.classifier = nn.Conv2d(out_channel, 2, kernel_size=(1, 1))
        self.flatten = nn.Flatten()
        self.batch_norm = nn.BatchNorm2d(2, affine=False)
        self.softmax = nn.Softmax(dim=-1)

    def forward(self, x):
        block_zip = zip(self.convnext_blocks, self.time_downsampling_blocks)
        for convnext_block, time_downsampling_block in block_zip:
            x = time_downsampling_block(x)
            x = convnext_block(x)
        x = self.octave_pool(x)
        x = self.global_average_pool(x)
        x = self.classifier(x)
        x = self.batch_norm(x)
        x = self.flatten(x)
        x = self.softmax(x / self.temperature)
        assert x.shape[1] == 24

        return x
