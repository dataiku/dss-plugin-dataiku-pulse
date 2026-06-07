# Architecture

Placeholder for V3 architecture documentation.

## Hub and Spoke Diagram

```mermaid
flowchart LR
    subgraph Workers[Worker DSS Instances]
        W1[Worker Project A]
        W2[Worker Project B]
        WN[Worker Project N]
    end

    subgraph Hub[Hub DSS Project]
        MF[Managed Folder / Shared Storage]
        GOLD[GOLD Builder / DuckDB]
        DASH[Pulse Dashboard]
    end

    W1 --> MF
    W2 --> MF
    WN --> MF
    MF --> GOLD
    GOLD --> DASH
```

This document should eventually describe:

- collection flow from workers
- normalization and GOLD build process
- dashboard consumption path
- hub/worker responsibilities
