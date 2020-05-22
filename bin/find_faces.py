#!/usr/bin/python3

# -*- coding: utf-8 -*-
import click
import PIL.Image
import numpy as np
import face_recognition
from face_recognition import api as fr
import json
import uuid

percision = 5
scaling = 1.65

@click.command()
@click.argument('file')
@click.option('--model', default="cnn", help='Face detection model, options are "hog" or "cnn".')
def main(file, model):
    im = PIL.Image.open(file)
    im = im.convert("RGB")
    image = np.array(im)

    locations = fr.face_locations(image, number_of_times_to_upsample=2, model=model)
    encodings = fr.face_encodings(image, known_face_locations=locations, model="default")
    faces = []

    for i, encoding in enumerate(encodings):
        width = locations[i][1] - locations[i][3]
        height = locations[i][2] - locations[i][0]

        x = round((locations[i][3] + (width / 2)) / im.width, percision)
        y = round((locations[i][0] + (height / 2)) / im.height, percision)
        w = round((width * scaling) / im.width, percision)
        h = round((height * scaling) / im.height, percision)
        id = str(uuid.uuid4())

        faces.append({
            "id": id,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "detector": "face_recognition@" + face_recognition.__version__,
            "confidence": 1,
            "encoding": list(encoding)
        })

    print(json.dumps(faces))

if __name__ == "__main__":
    main()


#
# https://scikit-learn.org/stable/modules/model_persistence.html
# https://github.com/ageitgey/face_recognition/blob/master/examples/face_recognition_svm.py
#
# Detect faces and patterns in image
# Save to the image node
# Take all the persisted models for all people and run predict/recognizion
# - Connect people
# When a person is manually connected to a face
# - Use the pattern to retrain the model of that person (always?) (different models for different ages?)
