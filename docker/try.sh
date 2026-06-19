#!/usr/bin/env bash

(
  set -x
  gpu-dev reserve --dockerfile Dockerfile --gpu-type H200 --gpus 1
)
