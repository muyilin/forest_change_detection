import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import init
from torch.autograd import Variable

USE_CUDA = torch.cuda.is_available()
DEVICE = torch.device("cuda:0")
def to_cuda(v):
    if USE_CUDA:
        return v.cuda(DEVICE)
    return v

class conv_block(nn.Module):
    def __init__(self,ch_in,ch_out):
        super(conv_block,self).__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(ch_in, ch_out, kernel_size=3,stride=1,padding=1,bias=True),
            nn.BatchNorm2d(ch_out),
            nn.ReLU(inplace=True),
            nn.Conv2d(ch_out, ch_out, kernel_size=3,stride=1,padding=1,bias=True),
            nn.BatchNorm2d(ch_out),
            nn.ReLU(inplace=True)
        )

    def forward(self,x):
        x = self.conv(x)
        return x


class up_conv(nn.Module):
    def __init__(self,ch_in,ch_out):
        super(up_conv,self).__init__()
        self.up = nn.Sequential(
            nn.Upsample(scale_factor=2),
            nn.Conv2d(ch_in,ch_out,kernel_size=3,stride=1,padding=1,bias=True),
            nn.BatchNorm2d(ch_out),
            nn.ReLU(inplace=True)
        )

    def forward(self,x):
        x = self.up(x)
        return x


class RNNCell(nn.Module):
    def __init__(self, input_size, hidden_size):
        super(RNNCell, self).__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.in_gate = nn.Conv2d(input_size + hidden_size, hidden_size, 3, 1, 1)
        self.forget_gate = nn.Conv2d(input_size + hidden_size, hidden_size, 3, 1, 1)
        self.out_gate = nn.Conv2d(input_size + hidden_size, hidden_size, 3, 1, 1)
        self.cell_gate = nn.Conv2d(input_size + hidden_size, hidden_size, 3, 1, 1)

    def forward(self, input, h_state, c_state):

        conc_inputs = torch.cat( (input, h_state), 1)

        in_gate = self.in_gate(conc_inputs)
        forget_gate = self.forget_gate(conc_inputs)
        out_gate = self.out_gate(conc_inputs)
        cell_gate = self.cell_gate(conc_inputs)

        in_gate = torch.sigmoid(in_gate)
        forget_gate = torch.sigmoid(forget_gate)
        out_gate = torch.sigmoid(out_gate)
        cell_gate = torch.tanh(cell_gate)

        c_state = (forget_gate * c_state) + (in_gate * cell_gate)
        h_state = out_gate * torch.tanh(c_state)

        return h_state, c_state


class set_values(nn.Module):
    def __init__(self, hidden_size, height, width):
            super(set_values, self).__init__()
            self.hidden_size=hidden_size
            self.height=int(height)
            self.width=int(width)
            self.dropout = nn.Dropout(0.2)
            self.RCell = RNNCell(self.hidden_size, self.hidden_size)


    def forward(self, seq, xinp):
        xout = to_cuda(Variable(torch.zeros(xinp.size()[0], xinp.size()[1], self.hidden_size, self.height, self.width)))

        h_state, c_state = ( to_cuda(Variable(torch.zeros(xinp.shape[0], self.hidden_size, self.height, self.width))),
                             to_cuda(Variable(torch.zeros(xinp.shape[0], self.hidden_size, self.height, self.width))) )

        for t in range(xinp.size()[1]):
            input_t = seq(xinp[:,t])
            xout[:,t] = input_t
            h_state, c_state = self.RCell(input_t, h_state, c_state)
        
        return self.dropout(h_state), xout



class ULSTMNet(nn.Module):
    def __init__(self, img_ch, output_ch, patch_size):
        super(ULSTMNet,self).__init__()

        self.patch_size = patch_size
        self.Maxpool = nn.MaxPool2d(kernel_size=2,stride=2)

        self.Conv1 = conv_block(ch_in=img_ch,ch_out=16)
        self.set1 = set_values(16, self.patch_size, self.patch_size)

        self.Conv2 = conv_block(ch_in=16,ch_out=32)
        self.set2 = set_values(32, self.patch_size/2, self.patch_size/2)

        self.Conv3 = conv_block(ch_in=32,ch_out=64)
        self.set3 = set_values(64, self.patch_size/4, self.patch_size/4)
        '''
        self.Conv4 = conv_block(ch_in=64,ch_out=128)
        self.set4 = set_values(128, self.patch_size/8, self.patch_size/8)

        self.Conv5 = conv_block(ch_in=128,ch_out=256)
        self.set5 = set_values(256, self.patch_size/16, self.patch_size/16)

        self.Up5 = up_conv(ch_in=256,ch_out=128)
        self.Up_conv5 = conv_block(ch_in=256, ch_out=128)

        self.Up4 = up_conv(ch_in=128,ch_out=64)
        self.Up_conv4 = conv_block(ch_in=128, ch_out=64)
        '''
        self.Up3 = up_conv(ch_in=64,ch_out=32)
        self.Up_conv3 = conv_block(ch_in=64, ch_out=32)

        self.Up2 = up_conv(ch_in=32,ch_out=16)
        self.Up_conv2 = conv_block(ch_in=32, ch_out=16)

        self.Conv_1x1 = nn.Conv2d(16,output_ch,kernel_size=1,stride=1,padding=0)


    def encoder(self, x):
        x1, xout = self.set1(self.Conv1, x)

        x2, xout = self.set2( nn.Sequential(self.Maxpool, self.Conv2), xout)

        x3, xout = self.set3( nn.Sequential(self.Maxpool, self.Conv3), xout)

        return x1,x2,x3

    def forward(self, x):
        xs = []
        for tensor_x in x:
            xs.append(tensor_x.unsqueeze(1))
        x = torch.cat(xs, 1)
        
        x1,x2,x3 = self.encoder(x)
        
        d3 = self.Up3(x3)
        d3 = torch.cat((d3,x2),dim=1)
        d3 = self.Up_conv3(d3)

        d2 = self.Up2(d3)
        d2 = torch.cat((d2,x1),dim=1)
        d2 = self.Up_conv2(d2)

        d1 = self.Conv_1x1(d2)

        return d1


import segmentation_models_pytorch as smp
from .convlstm import ConvLSTM

class conv3d_block(nn.Module):
    def __init__(self,ch_in,ch_out, kernel_size):
        super(conv3d_block,self).__init__()
        self.conv = nn.Sequential(
            nn.Conv3d(ch_in, ch_out, kernel_size=(1,kernel_size,kernel_size),stride=1,padding=0,bias=True),
            nn.BatchNorm3d(ch_out),
            nn.ReLU(inplace=True),
            nn.Conv3d(ch_out, ch_out, kernel_size=(1,kernel_size,kernel_size),stride=1,padding=0,bias=True),
            #nn.BatchNorm3d(ch_out),
            #nn.ReLU(inplace=True)
        )

    def forward(self,x):
        x = self.conv(x)
        return x

class Unet_LstmDecoder(torch.nn.Module):
    def __init__(self, num_channels, all_masks=None):
        super().__init__()

        self.encoder_depth = 3
        self.all_masks = all_masks

        self.unet = smp.Unet('resnet18', encoder_weights=None, encoder_depth= self.encoder_depth, decoder_channels=(256, 128, 64), classes=1)
        self.unet.encoder.conv1 = nn.Conv2d( num_channels, 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        self.convlstm = ConvLSTM(input_dim=1, hidden_dim=1, kernel_size=(3, 3), num_layers=8, batch_first=True, return_all_layers=False)
        
        self.conv3d = conv3d_block(1,1,1)
        self.conv3d_1x1 = nn.Conv3d(1, 1, kernel_size=1,stride=1,padding=0,bias=True),
    
    def forward(self, seq):
        unet_outs = []
        for x in seq:
            unet_outs.append(self.unet(x))
        unet_outs = torch.stack(unet_outs)

        last_outs,_ = self.convlstm(unet_outs)

        if not self.all_masks:
            return last_outs[0][-1].unsqueeze(1)
        else:
            out = torch.stack(last_outs).squeeze()
            if out.ndim==3:
                out = out.unsqueeze(0)
            return out
    
    def predict(self, x):
        if self.training:
            self.eval()
        with torch.no_grad():
            x = self.forward(x)
        return x
