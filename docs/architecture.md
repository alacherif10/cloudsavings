# CloudSavings — Architecture

## Purpose of this document

Explain **how the code is organised and why**, so that a future
contributor (or a future version of me) can navigate the project
without reverse-engineering it from the tests.

This document does **not** try to be exhaustive. It focuses on the
boundaries between modules and the design rules that keep them clean.

## High-level view

The project is a **local, single-user, offline** application. It runs
on Windows, uses only free and open-source libraries, and never
contacts AWS or any other external service.
