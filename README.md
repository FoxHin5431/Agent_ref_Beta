# Agent Ref (Beta)

Agent Ref is a reference-checking tool I built to help with a problem I kept running into in student work: references that look plausible, but may be inaccurate, incomplete or, in some cases, potentially fabricated.

I developed Agent Ref as a biology lecturer working with academic-integrity cases, using AI-assisted coding and testing it against the kinds of references I actually see in assessment.

The aim is not to decide whether a student has committed academic misconduct. Agent Ref is a screening and decision-support tool. It checks what it can, shows where the evidence agrees or conflicts, and leaves uncertain cases for a person to review.

**Try the current beta:**  
https://agent-ref-beta.streamlit.app/

> Agent Ref is an independent beta project developed by Mark Hintze, Lecturer in Biology at The Open University. It is not an official Open University service.

## What it does

Agent Ref accepts raw reference lists and tries to verify each citation against trusted metadata sources.

It currently works best with Harvard-style references and can handle a mixture of:

- journal articles
- references with or without DOIs
- PubMed and PMC records
- preprints
- websites and organisational sources
- databases and datasets
- incomplete or inconsistent citations
- references with incorrect bibliographic information

Where a reference can be verified, Agent Ref reports the evidence it found. Where the metadata do not agree, or the source is not well suited to automated checking, it flags the reference for review rather than automatically treating it as false.

## Why I built it

The project started from a fairly simple question: if a reference in student work looks suspicious, can I check it quickly and consistently without relying on AI-writing detectors or assuming that a failed search means the reference is fabricated?

That led to a workflow based on checking the cited record itself.

For academic-integrity work, that distinction matters. A missing DOI, a typo in a year, a badly formatted Harvard reference, or a legitimate website should not be treated in the same way as a citation that points to a completely different paper.

Agent Ref is designed around that problem.

## How verification works

Agent Ref currently uses evidence from sources including:

- Crossref
- DOI.org
- PubMed / PMC
- arXiv
- NASA ADS
- publisher and web metadata where appropriate

The validator compares identifiers and bibliographic information such as:

- title
- authors
- year
- journal or source
- DOI and other identifiers

Author checking uses the surnames explicitly supplied in the reference. For references using `et al.`, the named authors before `et al.` are checked against the trusted record.

The tool also keeps uncertain cases visible. If a source cannot be verified confidently, the result should remain something for a human reviewer to consider.

Links containing `utm_source=chatgpt.com` are marked as an advisory provenance signal. This does not change the verification result by itself.

## Pilot and testing

I am currently piloting Agent Ref in my work at The Open University and collecting data on how it performs in practice.

The pilot is being used to look at things such as:

- how often genuine references are correctly verified
- where false positives occur
- which types of references still need manual review
- how useful the output is in an academic-integrity workflow

I also maintain a separate development benchmark of roughly 180 references. This includes genuine references, deliberately altered references, metadata mismatches and difficult real-world citation formats.

In one recent small test, Agent Ref extracted and processed 25 raw Harvard-style references in around 7 seconds.

That benchmark is for development and regression testing rather than a formal performance claim.

## Current limitations

Agent Ref is still a beta project.

At the moment:

- Harvard-style references are the main supported input format
- malformed references can still expose parsing edge cases
- websites, databases and other non-journal sources often need different handling from journal articles
- external metadata services can be unavailable or disagree with one another
- a successful verification does not mean a reference is appropriate for an assignment
- an unsuccessful verification does not mean a reference is fabricated

Results should always be interpreted by a human reviewer.

## Roadmap

Current development priorities include:

- APA support
- Vancouver support
- Nature-style references
- more tolerant parsing of malformed citations
- improved handling of websites, reports, datasets and organisational sources
- additional subject-specific metadata sources
- continued pilot testing and validation

The longer-term aim is to make the verification layer as citation-style agnostic as possible: different reference formats should be normalised first, then checked using the same evidence-based validation process.

## Deployment

Current beta deployment:

https://agent-ref-beta.streamlit.app/


## About

Agent Ref is a beta reference-verification project developed for academic-integrity and assessment workflows.

It is built with AI-assisted software development, but the project is driven by a practical academic use case, reference-validation testing and human review.

## AI-assisted development

Agent Ref was developed with extensive use of ChatGPT by OpenAI for code generation, debugging, refactoring, test development and documentation.

The project design, validation logic, benchmark construction, testing decisions and academic-integrity use case were directed and reviewed by Mark Hintze.

AI-generated code and suggestions were reviewed and tested before being incorporated into the project
