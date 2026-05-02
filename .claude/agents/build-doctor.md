# Build Doctor Skill

Diagnoses and fixes COREFORGE build configuration issues automatically.

## Purpose
Prevents 80K+ token debugging sessions by detecting and fixing common build problems:
- Webpack/CRACO configuration issues
- PostCSS/Tailwind CSS processing failures
- Port mismatches between Tauri and dev servers
- Missing dependencies or broken build pipelines

## Usage
When user reports:
- "CSS not loading" or "styles broken"
- "Tauri window won't open"
- "Build failing" or "compilation errors"
- FirstRunSetup or any component displaying without styles

## What This Skill Does

### 1. Port Mismatch Detection
**Problem Learned:** Tauri waiting on port 3005, webpack serving on 3000
```bash
# Check tauri.conf.json for devPath/beforeDevCommand port
grep -A5 "beforeDevCommand" src-tauri/tauri.conf.json

# Check what port webpack is actually using
netstat -ano | grep "300[0-9]" || ss -tlnp | grep "300[0-9]"
```

### 2. CSS Processing Validation
**Problem Learned:** @apply directives not being processed by PostCSS
```bash
# Test if Tailwind CLI can process the CSS
npx tailwindcss -i ./src/index.css -o ./test-output.css

# Check for unprocessed directives in output
if grep -q "@apply\|@tailwind" test-output.css; then
  echo "ERROR: Tailwind not processing directives"
  echo "CAUSE: CRACO not invoking PostCSS correctly"
fi
```

### 3. CRACO/PostCSS Configuration
**Problem Learned:** PostCSS config correct but webpack not using it
```bash
# Verify PostCSS plugins are installed
npm ls tailwindcss autoprefixer postcss

# Check if CRACO is configured correctly
if ! grep -q "postcss" craco.config.js; then
  echo "ERROR: CRACO missing PostCSS configuration"
fi
```

### 4. @import vs Inline CSS
**Problem Learned:** @apply doesn't work in @import-ed files
```bash
# Check if index.css uses @import for theme files
if grep -q "@import.*theme" src/index.css; then
  echo "WARNING: @apply directives in imported files won't process"
  echo "SOLUTION: Inline the theme CSS into index.css"
fi
```

### 5. Quick Fixes

#### Fix Port Mismatch
```javascript
// Read actual webpack port from package.json or dev server output
const webpackPort = 3000; // detected port
// Update tauri.conf.json devPath to match
```

#### Fix CSS Processing
```bash
# Generate properly processed CSS
npx tailwindcss -i ./src/index.css -o ./src/index.processed.css

# For production builds, copy to build directory
find build/static/css -name "main.*.css" -exec cp src/index.processed.css {} \;
```

#### Fix @import Issue
```bash
# Inline imported theme files
cat src/theme/*.css >> src/index.css
# Remove @import statements
sed -i '/@import/d' src/index.css
```

## Expected Output

```markdown
## Build Doctor Report

### ✅ Checks Passed
- npm dependencies installed
- Rust/Cargo configured correctly
- TypeScript compiles (with warnings)

### ❌ Issues Found

**1. Port Mismatch**
- Tauri expects: localhost:3005
- Webpack serves: localhost:3000
- **Fix:** Update tauri.conf.json devPath to "http://localhost:3000"

**2. CSS Processing Failure**
- @apply directives not processed
- Found in: build/static/css/main.409f62a2.css
- **Fix:** Run `npx tailwindcss -i src/index.css -o src/index.processed.css`

**3. @import Blocking Tailwind**
- src/index.css imports theme files
- **Fix:** Inline theme CSS directly into index.css

### 🔧 Auto-Applied Fixes
- ✅ Generated processed CSS: test-output.css
- ✅ Updated tauri.conf.json port to 3000
- ✅ Copied processed CSS to build directory

### 🚀 Next Steps
1. Restart Tauri dev server: `npm run tauri:dev`
2. Tauri window should open automatically
3. FirstRunSetup will display with proper styling
```

## Agent Assignment
- **Primary:** general-purpose agent (first-line diagnosis)
- **Fallback:** QA agent (for comprehensive build validation)
- **MCP Access:** filesystem (read configs, check build output)

## Token Savings
- **Before:** 80K tokens debugging CSS/port issues manually
- **After:** 2-5K tokens running this skill
- **Savings:** ~75K tokens (94% reduction)

## Learning from Session
This skill encodes the following discoveries from 2025-10-24:
1. Tauri port mismatch causes window not to open
2. CRACO doesn't always invoke PostCSS correctly
3. @apply directives in @import-ed files don't get processed
4. Tailwind CLI can always process CSS correctly as fallback
5. Production builds need CSS copied after processing
