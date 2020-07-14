from __future__ import division
import os
import cv2
import time
import torch
import random
import argparse
import numpy as np
import pandas as pd
import pickle as pkl
import os.path as osp
import torch.nn as nn
from src.darknet import Darknet
from torch.autograd import Variable
from src.util import load_classes, prep_image, write_results


def arg_parse():
    """Detect file argument configuration"""

    parser = argparse.ArgumentParser(description='YOLO v3 Detection Module')

    parser.add_argument("--images", dest='images',
                        help="""Image / Directory containing
                                images to perform detection upon""",
                        default="imgs", type=str)
    parser.add_argument("--det", dest='det',
                        help="Image / Directory to store detections to",
                        default="det", type=str)
    parser.add_argument("--bs", dest="bs", help="Batch size", default=1)
    parser.add_argument("--confidence", dest="confidence",
                        help="Object Confidence to filter predictions",
                        default=0.5)
    parser.add_argument("--nms_thresh", dest="nms_thresh",
                        help="NMS Threshhold", default=0.4)
    parser.add_argument("--cfg", dest='cfg_file', help="Config file",
                        default="cfg/yolov3.cfg", type=str)
    parser.add_argument("--weights", dest='weights_file', help="weightsfile",
                        default="weights/yolov3.weights", type=str)
    parser.add_argument("--reso", dest='reso',
                        help="""Input resolution of the network. Increase to
                        increase accuracy. Decrease to increase speed""",
                        default="416", type=str)
    parser.add_argument("--CUDA", dest="CUDA",
                        help="GPU Acceleration Enable Flag (true/false)",
                        default="True", type=str)

    return parser.parse_args()


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def box_write(x, results) -> np.ndarray:
    """Returns the image where the object detection bounding boxes, labels
    and class confidences are printed on

    Arguments:
        x (torch.Tensor) : output results of the Darknet detection
        results (torch.Tensor) : loaded images tensor with a certain batch
    """
    c1 = tuple(x[1:3].int())
    c2 = tuple(x[3:5].int())
    img = results[int(x[0])]
    cls = int(x[-1])
    color = random.choice(colors)
    label = "{0} {1:.4}".format(classes[cls], x[-2])
    cv2.rectangle(img, c1, c2, color, 1)
    t_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_PLAIN, 1, 1)[0]
    c2 = c1[0] + t_size[0] + 3, c1[1] + t_size[1] + 4
    cv2.rectangle(img, c1, c2, color, -1)
    cv2.putText(img, label, (c1[0], c1[1] + t_size[1] + 4),
                cv2.FONT_HERSHEY_PLAIN, 1, [225, 255, 255], 1)
    return img


# configuration by arguments
args = arg_parse()
images = args.images
batch_size = int(args.bs)
confidence = float(args.confidence)
nms_thesh = float(args.nms_thresh)
start = 0
CUDA = str2bool(torch.cuda.is_available() and str2bool(args.CUDA))
print(type(CUDA))
assert type(CUDA) == bool
metrics = []

num_classes = 80
classes = load_classes("data/coco.names")


# set up the darknet
print("Loading network.....")
model = Darknet(args.cfg_file, CUDA)
model.load_weights(args.weights_file)
print("Network successfully loaded")

model.net_info["height"] = args.reso
inp_dim = int(model.net_info["height"])

# input dimension check
assert inp_dim % 32 == 0
assert inp_dim > 32

# if there is multiple GPU, use parallel pass
if CUDA and torch.cuda.device_count() > 1:
    model == nn.DataParallel(model)
# if there is only one GPU, use only this one
elif CUDA:
    model.cuda()


# set the model in evaluation mode
model.eval()

read_dir = time.time()
# detection phase
try:
    imlist = [osp.join(osp.realpath('.'), images, img)
              for img in os.listdir(images)]
except NotADirectoryError:
    imlist = []
    imlist.append(osp.join(osp.realpath('.'), images))
except FileNotFoundError:
    print("No file or directory with the name {}".format(images))
    exit()

# if the destination path doesn't exist
if not os.path.exists(args.det):
    os.makedirs(args.det)

load_batch = time.time()
loaded_ims = [cv2.imread(x) for x in imlist]

im_batches = list(map(prep_image, loaded_ims, [
                  inp_dim for x in range(len(imlist))]))
im_dim_list = [(x.shape[1], x.shape[0]) for x in loaded_ims]
im_dim_list = torch.FloatTensor(im_dim_list).repeat(1, 2)


leftover = 0
if (len(im_dim_list) % batch_size):
    leftover = 1

if batch_size != 1:
    num_batches = len(imlist) // batch_size + leftover
    im_batches = [torch.cat((im_batches[i*batch_size: min((i + 1)*batch_size,
                             len(im_batches))])) for i in range(num_batches)]

write = 0


if CUDA:
    im_dim_list = im_dim_list.cuda()

start_det_loop = time.time()
for i, batch in enumerate(im_batches):
    # load the image
    start = time.time()
    if CUDA:
        batch = batch.cuda()
    with torch.no_grad():
        prediction = model(Variable(batch))

    prediction = write_results(prediction, confidence,
                               num_classes, nms_conf=nms_thesh)

    end = time.time()

    if type(prediction) == int:

        for im_num, image in enumerate(imlist[i*batch_size:
                                       min((i + 1)*batch_size, len(imlist))]):
            im_id = i*batch_size + im_num
            print("{0:20s} predicted in {1:6.3f} seconds".format(
                image.split("/")[-1], (end - start)/batch_size))
            print("{0:20s} {1:s}".format("Objects Detected:", ""))
            print("----------------------------------------------------------")
        continue

    # transform the atribute from index in batch to index in imlist
    prediction[:, 0] += i*batch_size

    if not write:  # If we have't initialised output
        output = prediction
        write = 1
    else:
        output = torch.cat((output, prediction))

    for im_num, image in enumerate(imlist[i*batch_size:
                                   min((i + 1)*batch_size, len(imlist))]):
        im_id = i*batch_size + im_num
        objs = [classes[int(x[-1])] for x in output if int(x[0]) == im_id]
        print("{0:20s} predicted in {1:6.3f} seconds".format(
            image.split("/")[-1], (end - start)/batch_size))
        print("{0:20s} {1:s}".format("Objects Detected:", " ".join(objs)))
        print("----------------------------------------------------------")

        # writing the metrics for each file and each class
        text = ""
        for x in output:
            if int(x[0]) == im_id:
                bbox_text = "\t\tBounding Box: {}\n".format(
                    (torch.round(x)[1:5].tolist()))
                text += ''.join(["\tClass: {}\n".format(classes[int(x[-1])]),
                                 bbox_text,
                                 "\t\tObjectness= {:.4f}\n".format(x[-3]),
                                 "\t\t Class Conf.= {:.4f}\n".format(x[-2])])
        t = (end-start)/batch_size
        text = ''.join([image.split("/")[-1] + "\n",
                        "\tTime (s): {:.4f}\n".format(t)]) + text
        metrics.append(text)

    if CUDA:
        torch.cuda.synchronize()
try:
    output
except NameError:
    print("No detections were made")
    exit()

im_dim_list = torch.index_select(im_dim_list, 0, output[:, 0].long())

scaling_factor = torch.min(416/im_dim_list, 1)[0].view(-1, 1)


output[:, [1, 3]] -= (inp_dim - scaling_factor*im_dim_list[:, 0].view(-1, 1))/2
output[:, [2, 4]] -= (inp_dim - scaling_factor*im_dim_list[:, 1].view(-1, 1))/2


output[:, 1:5] /= scaling_factor

for i in range(output.shape[0]):
    output[i, [1, 3]] = torch.clamp(output[i, [1, 3]], 0.0, im_dim_list[i, 0])
    output[i, [2, 4]] = torch.clamp(output[i, [2, 4]], 0.0, im_dim_list[i, 1])


output_recast = time.time()
class_load = time.time()
colors = pkl.load(open("weights/pallete", "rb"))

draw = time.time()

list(map(lambda x: box_write(x, loaded_ims), output))

det_names = pd.Series(imlist).apply(
    lambda x: "{}/det_{}_{}".format(args.det,
                                    args.cfg_file[4:-4],
                                    x.split("/")[-1]))

list(map(cv2.imwrite, det_names, loaded_ims))


end = time.time()

print("SUMMARY")
print("----------------------------------------------------------")
print("{:25s}: {}".format("Task", "Time Taken (in seconds)"))
print()
print("{:25s}: {:2.3f}".format("Reading addresses", load_batch - read_dir))
print("{:25s}: {:2.3f}".format("Loading batch", start_det_loop - load_batch))
print("{:25s}: {:2.3f}".format("Detection (" + str(len(imlist)) +
                               " images)", output_recast - start_det_loop))
print("{:25s}: {:2.3f}".format("Output Processing",
                               class_load - output_recast))
print("{:25s}: {:2.3f}".format("Drawing Boxes", end - draw))
print("{:25s}: {:2.3f}".format("Average time_per_img",
                               (end - load_batch)/len(imlist)))
print("----------------------------------------------------------")

# writing the metrics to file
metrics_file = args.det + '/metrics.txt'
metrics_file = open(metrics_file, "w")
metrics_file.write("".join(metrics))
metrics_file.close()

# empty cuda cash
torch.cuda.empty_cache()
